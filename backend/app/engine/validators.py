import re
import bleach
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional, List
from dateutil.parser import parse
from app.schemas.agent import FieldDefinition

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

try:
    from email_validator import validate_email, EmailNotValidError

    HAS_EMAIL_VALIDATOR = True
except ImportError:
    HAS_EMAIL_VALIDATOR = False

try:
    import phonenumbers
    from phonenumbers.phonenumberutil import NumberParseException

    HAS_PHONE_NUMBERS = True
except ImportError:
    HAS_PHONE_NUMBERS = False


def _is_monotonic_run(digits: str) -> bool:
    """True for strictly ascending/descending digit runs (e.g. 1234567890).

    Allows the 9->0 wrap so full-keypad walks are caught. Single-step
    comparison only — no reference to any specific number.
    """
    if len(digits) < 7:
        return False
    asc = all(
        (int(digits[i + 1]) - int(digits[i])) % 10 == 1 for i in range(len(digits) - 1)
    )
    desc = all(
        (int(digits[i]) - int(digits[i + 1])) % 10 == 1 for i in range(len(digits) - 1)
    )
    return asc or desc


def _looks_like_fake_number(digits: str) -> bool:
    """Heuristic fake-number screen for the lenient digit fallback.

    Catches keypad walks (1234567890), near-uniform strings (1000000001 —
    only 2 distinct digits across 10+ digits), and zeros-heavy placeholders.
    Real subscriber numbers almost always carry 4+ distinct digits. Generic
    pattern analysis — no blocklist of specific numbers.
    """
    core = digits[-10:] if len(digits) >= 10 else digits
    if len(core) >= 10 and len(set(core)) <= 2:
        return True
    if _is_monotonic_run(core):
        return True
    if len(core) >= 10 and core.count("0") >= len(core) - 2:
        return True
    return False


class DataValidator:
    """
    The strict Python backend boundary that verifies LLM extracted entities
    before they are ever written to the database state.

    Dispatches validation by field TYPE (from FieldDefinition) instead of
    hardcoded field NAME, making it fully dynamic for custom fields.
    """

    @staticmethod
    def _validate_date(
        date_str: str, tz_name: str = "UTC", max_days_ahead: int = 90
    ) -> Tuple[Optional[str], Optional[str]]:
        try:
            parsed_date = parse(date_str)
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
            now_in_tz = datetime.now(tz).date()
            if parsed_date.date() < now_in_tz:
                return None, "Booking date cannot be in the past."
            max_date = now_in_tz + timedelta(days=max_days_ahead)
            if parsed_date.date() > max_date:
                return (
                    None,
                    f"Booking date cannot be more than {max_days_ahead} days in the future.",
                )
            return parsed_date.date().isoformat(), None
        except ValueError:
            return (
                None,
                "Invalid date format. Please use YYYY-MM-DD or say something like 'next Tuesday'.",
            )

    @staticmethod
    def _validate_time(time_str: str) -> Tuple[Optional[str], Optional[str]]:
        raw = (time_str or "").strip().lower()
        if not raw:
            return (
                None,
                "Invalid time format. Please provide a clear time like '2 PM' or '14:30'.",
            )

        is_date_only = bool(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw)
            or re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", raw)
        )
        # Normalise "3.30 pm" -> "3:30 pm", "3pm" -> "3 pm" for token detection (mirrors time_flex)
        _norm_raw = raw.replace(".", ":")
        _norm_raw = re.sub(r"(\d)(am|pm)\b", r"\1 \2", _norm_raw)
        has_explicit_time_token = bool(
            _norm_raw in {"noon", "midnight"}
            or re.search(r"(am|pm)\b", _norm_raw)
            or re.search(r"\b\d{1,2}:\d{2}\b", _norm_raw)
        )

        # Reject date-only / ambiguous strings that dateutil would coerce to 00:00:00.
        if is_date_only or not has_explicit_time_token:
            return (
                None,
                "Invalid time format. Please provide a clear time like '2 PM' or '14:30'.",
            )

        if raw == "noon":
            return "12:00:00", None
        if raw == "midnight":
            return "00:00:00", None

        try:
            # Normalise for dateutil: "3.30pm" -> "3:30 pm", "3pm" -> "3 pm"
            _to_parse = re.sub(r"(\d)(am|pm)\b", r"\1 \2", str(time_str).strip().replace(".", ":"))
            parsed_time = parse(_to_parse)
            t = parsed_time.time()
            if t.hour > 23 or t.minute > 59:
                return (
                    None,
                    "Invalid time. Please provide a time like '2 PM' or '14:30'.",
                )
            return t.isoformat(), None
        except ValueError:
            return (
                None,
                "Invalid time format. Please provide a clear time like '2 PM' or '14:30'.",
            )

    @staticmethod
    def _validate_email(value: str) -> Tuple[Optional[str], Optional[str]]:
        value = value.strip().lower()
        if HAS_EMAIL_VALIDATOR:
            try:
                result = validate_email(value, check_deliverability=False)
                return result.normalized, None
            except EmailNotValidError as e:
                return None, f"Invalid email: {str(e)}"
        # Fallback regex (stronger than before)
        if re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", value):
            return value, None
        return None, "Invalid email format. Please provide a valid email address."

    @staticmethod
    def _validate_phone(
        value: str, country_code: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
        raw_input = value.strip()
        normalized_country = (country_code or "").strip().upper() or ""
        metadata = {
            "country_code": normalized_country,
            "raw_input": raw_input,
        }

        if HAS_PHONE_NUMBERS:
            try:
                parsed = phonenumbers.parse(raw_input, normalized_country or None)
                if phonenumbers.is_valid_number(parsed):
                    canonical = phonenumbers.format_number(
                        parsed, phonenumbers.PhoneNumberFormat.E164
                    )
                    # Even structurally valid ranges contain famous test
                    # sequences (9876543210). Screen those generically.
                    if _looks_like_fake_number(re.sub(r"\D", "", canonical)):
                        return None, "Please provide a valid phone number.", metadata
                    region = phonenumbers.region_code_for_number(parsed) or normalized_country
                    metadata["country_code"] = region or normalized_country
                    return canonical, None, metadata
            except NumberParseException:
                pass
            # WhatsApp free text carries no country selector. For this
            # deployment (IN org, Asia/Calcutta) a bare 10-digit mobile
            # starting 6-9 is Indian — retry as IN so Cloud/OTP gets a
            # routable +91 E.164 instead of a bare +<10 digits>.
            if not normalized_country:
                _digits_only = re.sub(r"\D", "", raw_input)
                if len(_digits_only) == 10 and _digits_only[0] in "6789":
                    try:
                        _parsed_in = phonenumbers.parse(_digits_only, "IN")
                        if phonenumbers.is_valid_number(_parsed_in):
                            if _looks_like_fake_number(_digits_only):
                                return (
                                    None,
                                    "Please provide a valid phone number.",
                                    metadata,
                                )
                            metadata["country_code"] = "IN"
                            return (
                                phonenumbers.format_number(
                                    _parsed_in, phonenumbers.PhoneNumberFormat.E164
                                ),
                                None,
                                metadata,
                            )
                    except NumberParseException:
                        pass
            # Not strictly valid per the national numbering plan (e.g. test
            # numbers like "+91 456781230"). Fall through to the lenient
            # digit-length check below instead of hard-rejecting the input.

        digits = re.sub(r"\D", "", value)
        if len(digits) < 10:
            return None, "Phone number must have at least 10 digits.", metadata
        if len(digits) > 15:
            return None, "Phone number is too long.", metadata
        # Reject obviously fake patterns (all same digit or sequential)
        if len(set(digits)) == 1:
            return None, "Please provide a valid phone number.", metadata
        if _looks_like_fake_number(digits):
            return None, "Please provide a valid phone number.", metadata
        return f"+{digits}", None, metadata

    @staticmethod
    def _validate_number(value: str) -> Tuple[Optional[str], Optional[str]]:
        cleaned = value.replace(",", "").strip()
        try:
            num = float(cleaned)
            if num < 0:
                return None, "Please enter a positive number."
            return cleaned, None
        except ValueError:
            return None, "Please enter a valid number."

    @staticmethod
    def _validate_select(
        value: str, options: List[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        if not options:
            return value, None
        lower_opts = {o.lower(): o for o in options}
        if value.lower() in lower_opts:
            return lower_opts[value.lower()], None
        return None, f"Please choose from: {', '.join(options)}"

    @staticmethod
    def _validate_text(value: str) -> Tuple[Optional[str], Optional[str]]:
        if len(value) > 255:
            return None, "Input too long (max 255 chars)."
        if len(value) < 1:
            return None, "This field cannot be empty."
        return value, None

    # Social chitchat is never a name ("thanks" was once saved as the
    # customer's name). Same sanitation concept as NULL_VALUES below:
    # greetings / thanks / farewells / bare acknowledgments carry no
    # identity, in any path (LLM extraction, offline fallback, graph).
    CHITCHAT_VALUES = frozenset({
        "hi", "hii", "hiii", "hello", "hey", "yo", "sup",
        "thanks", "thank you", "thankyou", "thx", "ty",
        "bye", "goodbye", "good morning", "good evening", "good afternoon",
        "good night", "morning", "evening",
        "ok", "okay", "sure", "yes", "no", "yep", "yup",
        "please", "sorry", "welcome", "great", "nice", "cool", "awesome",
        "how are you", "how r u", "whats up", "what's up",
    })

    @staticmethod
    def _validate_name(value: str) -> Tuple[Optional[str], Optional[str]]:
        cleaned_early = " ".join(str(value or "").split()).lower()
        if cleaned_early in DataValidator.CHITCHAT_VALUES:
            return (
                None,
                "That looks like a greeting, not a name. Please share your real name.",
            )
        if len(value) > 100:
            return None, "Name is too long (max 100 chars)."
        if len(value) < 2:
            return None, "Please provide your full name."
        # Names should not contain digits
        if any(c.isdigit() for c in value):
            return (
                None,
                "A name shouldn't contain numbers. Please provide your real name.",
            )
        # Must have meaningful alphabetic content (not just symbols)
        alpha_chars = sum(1 for c in value if c.isalpha())
        if alpha_chars < 2:
            return (
                None,
                "That doesn't look like a valid name. Please provide your real name.",
            )
        # Keyboard mashing ("Fhaaaaaaaa", "Heeee", "okkkkk") is never a name:
        # no real name repeats one letter 3+ times in a row.
        lowered = value.lower()
        if re.search(r"(.)\1\1", lowered):
            return (
                None,
                "That doesn't look like a real name. Please provide your full name.",
            )
        # Clean up extra whitespace
        cleaned = " ".join(value.split())
        return cleaned, None

    @staticmethod
    def _validate_textarea(value: str) -> Tuple[Optional[str], Optional[str]]:
        if len(value) > 2000:
            return None, "Input too long (max 2000 chars)."
        if len(value) < 1:
            return None, "This field cannot be empty."
        return value, None

    # Type → validator dispatch table
    TYPE_VALIDATORS = {
        "email": lambda v, fd, tz, md: DataValidator._validate_email(v),
        "phone": lambda v, fd, tz, md: DataValidator._validate_phone(v),
        "date": lambda v, fd, tz, md: DataValidator._validate_date(v, tz, md),
        "time": lambda v, fd, tz, md: DataValidator._validate_time(v),
        "number": lambda v, fd, tz, md: DataValidator._validate_number(v),
        "select": lambda v, fd, tz, md: DataValidator._validate_select(
            v, fd.options if fd else []
        ),
        "text": lambda v, fd, tz, md: DataValidator._validate_text(v),
        "textarea": lambda v, fd, tz, md: DataValidator._validate_textarea(v),
    }

    # Values that should be treated as "user didn't provide an answer"
    NULL_VALUES = {"none", "null", "n/a", "na", "nil", "nothing", "-", ""}

    @staticmethod
    def validate_entities(
        extracted_data: Dict[str, Any],
        field_definitions: Optional[List[FieldDefinition]] = None,
        timezone: str = "UTC",
        max_booking_days: int = 90,
        field_meta: Optional[Dict[str, Any]] = None,
        include_metadata: bool = False,
    ):
        """
        Validates extracted data using field definitions (type-aware).
        Returns (valid_data, error_messages)
        """
        valid_data = {}
        errors = {}
        metadata: Dict[str, Dict[str, str]] = {}

        fd_map: Dict[str, FieldDefinition] = {}
        if field_definitions:
            fd_map = {fd.name: fd for fd in field_definitions}

        for field, value in extracted_data.items():
            if value is None:
                continue
            if not isinstance(value, str):
                value = str(value)

            value = value.strip()

            # HTML sanitization — strip all HTML tags to prevent XSS/injection
            if isinstance(value, str):
                value = bleach.clean(value, tags=[], strip=True).strip()

            if value.lower() in DataValidator.NULL_VALUES:
                errors[field] = (
                    f"Please provide a valid value for {field.replace('_', ' ')}."
                )
                continue

            fd = fd_map.get(field)
            field_context = (
                field_meta.get(field, {})
                if isinstance(field_meta, dict) and isinstance(field_meta.get(field), dict)
                else {}
            )
            country_code = field_context.get("country_code")

            if field == "phone" or (fd and fd.type == "phone"):
                result, error, phone_meta = DataValidator._validate_phone(
                    value, country_code=country_code
                )
                if phone_meta:
                    metadata[field] = phone_meta
            elif field == "name":
                # The "name" field has no dedicated fd.type (defaults to
                # "text", which accepts anything) — route it through the
                # name validator so chitchat/greetings can never be saved
                # as the customer's name on any path.
                result, error = DataValidator._validate_name(value)
            elif fd:
                validator = DataValidator.TYPE_VALIDATORS.get(
                    fd.type, lambda v, _fd, _tz, _md: DataValidator._validate_text(v)
                )
                result, error = validator(value, fd, timezone, max_booking_days)
            else:
                if field == "email":
                    result, error = DataValidator._validate_email(value)
                elif field == "phone":
                    result, error, phone_meta = DataValidator._validate_phone(
                        value, country_code=country_code
                    )
                    if phone_meta:
                        metadata[field] = phone_meta
                elif field == "date":
                    result, error = DataValidator._validate_date(
                        value, timezone, max_booking_days
                    )
                elif field == "time":
                    result, error = DataValidator._validate_time(value)
                elif field == "name":
                    result, error = DataValidator._validate_name(value)
                else:
                    result, error = DataValidator._validate_text(value)

            if result is not None:
                valid_data[field] = result
            elif error:
                errors[field] = error

        if include_metadata:
            return valid_data, errors, metadata
        return valid_data, errors

# Playwright E2E Tests for Booking Widget

## Prerequisites

1. **Backend running** on port 8000:
   ```bash
   cd backend && python run.py
   ```

2. **Widget dev server running** on port 5174:
   ```bash
   cd frontend/chat-widget && npm run dev
   ```

3. **Install Playwright browsers**:
   ```bash
   cd frontend/chat-widget
   npx playwright install chromium
   ```

## Running Tests

### Stable gating profile (default)
Use this in CI or milestone verification. It excludes `@legacy-flaky` scenarios by default.

```bash
npm run test:e2e -- --grep "Booking Widget E2E Flow|Meeting Policy Visibility|Timezone-aware availability flow|Online Meeting Link Delivery"
```

### Targeted repeat run (date-picker hardening)
Use when validating deterministic date-picker behavior after flow changes.

```bash
npm run test:e2e -- --grep "should handle date picker" --repeat-each=5
```

### Isolated legacy-flaky profile (non-gating)
Keeps visibility of historical instability without blocking stable gates.

```bash
$env:E2E_PROFILE='legacy'; npm run test:e2e -- --grep "@legacy-flaky"
```

### Optional interactive runs

```bash
npm run test:e2e:ui
npm run test:e2e:headed
```

## Test Coverage

- `booking-flow.spec.ts` - Full user journey tests
  - Widget loads on host page
  - Greeting message displays
  - Send/receive messages
  - Name input field handling
  - Date picker interaction
  - Confirmation card display
  - Booking confirmed state
  - Error handling when backend unavailable
  - `@legacy-flaky` tags used for non-gating residual scenarios

## Configuration

Edit `playwright.config.ts` to customize:
- Browser selection
- Test timeout
- Retry behavior
- Screenshot options
- Profile routing (`E2E_PROFILE=gating|legacy`) for stable vs isolated suites

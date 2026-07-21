from fastapi import HTTPException

class NotFoundException(HTTPException):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=404, detail=detail)

class ForbiddenException(HTTPException):
    def __init__(self, detail: str = "Not enough permissions"):
        super().__init__(status_code=403, detail=detail)

class BadRequestException(HTTPException):
    def __init__(self, detail: str = "Bad request"):
        super().__init__(status_code=400, detail=detail)

class ConcurrencyException(HTTPException):
    def __init__(self, detail: str = "Concurrency conflict, please try again"):
        super().__init__(status_code=409, detail=detail)

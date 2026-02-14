from fastapi import HTTPException, status


class AuthenticationError(HTTPException):
    def __init__(self, detail: str = "Kimlik doğrulama başarısız"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class AuthorizationError(HTTPException):
    def __init__(self, detail: str = "Bu işlem için yetkiniz yok"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class NotFoundError(HTTPException):
    def __init__(self, detail: str = "Kayıt bulunamadı"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ConflictError(HTTPException):
    def __init__(self, detail: str = "Çakışma hatası"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class RateLimitError(HTTPException):
    def __init__(self, retry_after: int = 60):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla istek gönderdiniz. Lütfen bekleyin.",
            headers={"Retry-After": str(retry_after)},
        )


class OdooConnectionError(HTTPException):
    def __init__(self, detail: str = "Odoo ERP bağlantısı kurulamadı"):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=detail
        )


class DocumentProcessingError(HTTPException):
    def __init__(self, detail: str = "Döküman işlenirken hata oluştu"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
        )

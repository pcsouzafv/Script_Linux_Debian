class IntegrationError(RuntimeError):
    pass


class AuthorizationError(RuntimeError):
    pass


class ResourceNotFoundError(IntegrationError):
    pass

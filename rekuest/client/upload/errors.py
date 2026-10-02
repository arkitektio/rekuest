class IoError(Exception):
    """Base class for IO Errors"""

    pass


class UploadError(IoError):
    """Error while uploading to the DataLayer"""

    pass

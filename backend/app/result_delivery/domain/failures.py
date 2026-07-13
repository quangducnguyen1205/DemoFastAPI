class ProcessingResultPublisherError(RuntimeError):
    pass


class TransientProcessingResultPublisherError(ProcessingResultPublisherError):
    pass


class PermanentProcessingResultPublisherError(ProcessingResultPublisherError):
    pass


class ProcessingResultPublisherDisabledError(PermanentProcessingResultPublisherError):
    pass

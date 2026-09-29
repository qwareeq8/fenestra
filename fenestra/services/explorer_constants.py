"""Import-light constants shared by Explorer services and workers."""

FVM_DETAILS = 4

# The first entry is unused; the debounce uses DEBOUNCE_DELAY_MS. Later entries
# are minimum retry delays, which exponential backoff lengthens to 200, 400, 800,
# and 1,600 milliseconds.
DEFAULT_AUTOSIZE_RETRY_SCHEDULE = (0.05, 0.1, 0.25, 0.5, 1.0)

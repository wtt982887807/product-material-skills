"""Resolve exact, unambiguous spans in the source paragraph's visible text."""


def locate_span(text, before, start=None):
    """Return a zero-based Python character range, refusing fuzzy matches.

    An explicit offset disambiguates repeated text; without one the exact
    substring must occur once, including overlapping occurrences.
    """
    if not isinstance(text, str) or not isinstance(before, str) or not before:
        raise ValueError("Span requires text and nonempty before strings")
    if start is not None:
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("start must be a nonnegative integer source offset")
        end = start + len(before)
        if end > len(text) or text[start:end] != before:
            raise ValueError("before text does not match the specified source offset")
        return start, end
    start = text.find(before)
    if start < 0:
        raise ValueError("before text was not found in the source paragraph")
    if text.find(before, start + 1) >= 0:
        raise ValueError("Ambiguous before text; provide its exact source start offset")
    return start, start + len(before)

"""Per-course term lists, used to bias the decoder and to annotate the text.

Everything here is pure: it parses a term file into data and rewrites strings,
and never touches the disk beyond reading the glossary. Filled in during step 3.
"""

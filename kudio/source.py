"""Identity of untouched TXT/PCS source, shared by compilation and projects."""
import hashlib


def source_hash(text):
    """Keep the schema-2 SHA-256 of exact UTF-8 bytes, without normalization."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

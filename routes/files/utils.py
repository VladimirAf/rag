import hashlib
from io import BytesIO


def get_file_hash(file_input, algorithm='sha256'):
    """
    Get hash of a file or BytesIO object using specified algorithm.
    
    Args:
        file_input (str or BytesIO): File path or BytesIO object
        algorithm (str): Hash algorithm ('md5', 'sha1', 'sha256', etc.)
    
    Returns:
        str: Hexadecimal hash string
    """
    hash_obj = hashlib.new(algorithm)
    
    if isinstance(file_input, (str, bytes)):
        # File path
        with open(file_input, 'rb') as file:
            for chunk in iter(lambda: file.read(4096), b""):
                hash_obj.update(chunk)
    elif isinstance(file_input, BytesIO):
        # BytesIO object
        file_input.seek(0)  # Reset position to beginning
        for chunk in iter(lambda: file_input.read(4096), b""):
            hash_obj.update(chunk)
        file_input.seek(0)  # Reset position for potential future use
    else:
        raise ValueError("file_input must be a file path or BytesIO object")
    
    file_input.seek(0)
    return hash_obj.hexdigest()
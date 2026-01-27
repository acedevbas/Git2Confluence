import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.confluence.schema_utils import generate_example_from_schema
from datetime import datetime
import json

def test_datetime_serialization():
    schema = {
        "type": "object",
        "example": {
            "created_at": datetime.now(),
            "data": "some data"
        }
    }
    
    try:
        example = generate_example_from_schema(schema)
        json_output = json.dumps(example)
        print("SUCCESS: Serialization successful")
        print(f"Output: {json_output}")
    except TypeError as e:
        print(f"FAILURE: {e}")
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_datetime_serialization()

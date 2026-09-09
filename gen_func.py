import os
import json


def read_utbildningar_from_json():
    
    file_path = os.path.join(os.path.dirname(__file__), 'utbildningar.json')  # Adjust the path as needed
    # Read utbildningar from a JSON file.
    if not os.path.exists(file_path):
        return []  # Return an empty list if the file doesn't exist

    with open(file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)
        return data.get('utbildningar', [])  # Return the list of utbildningar or an empty list if not found
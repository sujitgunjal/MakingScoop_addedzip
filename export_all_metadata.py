import os
import json

HOME = os.path.expanduser("~")
APPS_DIR = os.path.join(HOME, "myscoop", "apps")

def export_all_metadata(output_file="all_metadata.json"):
    """
    Finds all metadata.json files in the installed myscoop applications
    and aggregates them into a single JSON file.
    """
    all_metadata = []
    
    if not os.path.exists(APPS_DIR):
        print(f"Apps directory not found: {APPS_DIR}")
        return

    for app_name in os.listdir(APPS_DIR):
        app_path = os.path.join(APPS_DIR, app_name)
        if not os.path.isdir(app_path):
            continue
            
        for version_dir in os.listdir(app_path):
            meta_path = os.path.join(app_path, version_dir, "metadata.json")
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)
                        data["_app_name"] = app_name
                        data["_app_version"] = version_dir
                        all_metadata.append(data)
                    except json.JSONDecodeError:
                        print(f"Error reading {meta_path}")
                        
    # Write aggregated data to the output file
    with open(output_file, "w", encoding="utf-8") as out:
        json.dump(all_metadata, out, indent=4, ensure_ascii=False)
        
    print(f"Exported metadata for {len(all_metadata)} apps to {os.path.abspath(output_file)}")

if __name__ == "__main__":
    export_all_metadata()

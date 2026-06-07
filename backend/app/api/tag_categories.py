# tag_categories.py

# A lightweight static dictionary to map common tags to categories.

TAG_CATEGORIES = {
    "1girl": "Character",
    "1boy": "Character",
    "solo": "Character",
    "multiple_girls": "Character",
    "multiple_boys": "Character",
    "2girls": "Character",
    "2boys": "Character",
    "long_hair": "Body",
    "short_hair": "Body",
    "blonde_hair": "Body",
    "black_hair": "Body",
    "silver_hair": "Body",
    "blue_eyes": "Body",
    "red_eyes": "Body",
    "breasts": "Body",
    "large_breasts": "Body",
    "smile": "Pose/Action",
    "standing": "Pose/Action",
    "sitting": "Pose/Action",
    "looking_at_viewer": "Pose/Action",
    "blush": "Pose/Action",
    "open_mouth": "Pose/Action",
    "shirt": "Clothing",
    "skirt": "Clothing",
    "dress": "Clothing",
    "school_uniform": "Clothing",
    "thighhighs": "Clothing",
    "gloves": "Clothing",
    "hat": "Clothing",
    "glasses": "Clothing",
    "jewelry": "Clothing",
    "outdoors": "Setting/Bg",
    "indoors": "Setting/Bg",
    "sky": "Setting/Bg",
    "cloud": "Setting/Bg",
    "day": "Setting/Bg",
    "night": "Setting/Bg",
    "simple_background": "Setting/Bg",
    "masterpiece": "Quality",
    "best_quality": "Quality",
    "highres": "Quality",
    "monochrome": "Style",
    "comic": "Style",
    "sketch": "Style"
}

def guess_category(tag: str) -> str:
    # 1. Exact match in predefined map
    if tag in TAG_CATEGORIES:
        return TAG_CATEGORIES[tag]
    
    # 2. Simple rules based on suffixes/prefixes
    if tag.endswith("_hair") or tag.endswith("_eyes") or "skin" in tag or "breasts" in tag:
        return "Body"
    if tag.endswith("_background") or tag in ["outdoors", "indoors"]:
        return "Setting/Bg"
    if ("shirt" in tag or "skirt" in tag or "dress" in tag or "uniform" in tag or 
        "pants" in tag or "jacket" in tag or "gloves" in tag or "boots" in tag or 
        "shoes" in tag or "socks" in tag or "ribbon" in tag or "bow" in tag or "necklace" in tag):
        return "Clothing"
    if "boy" in tag or "girl" in tag or "solo" in tag:
        return "Character"
    if "looking" in tag or "standing" in tag or "sitting" in tag or "holding" in tag or "smile" in tag:
        return "Pose/Action"
        
    # Default
    return "Other"

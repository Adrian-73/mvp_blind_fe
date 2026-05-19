import random

ADJECTIVES = [
    "Calm", "Swift", "Bold", "Quiet", "Bright", "Silver", "Golden", "Amber", "Wild", "Gentle",
    "Kind", "Tranquil", "Fierce", "Noble", "Eager", "Loyal", "Lucky", "Proud", "Happy", "Candid",
    "Frost", "Sunny", "Shadow", "Spirit", "Dusky", "Mist", "Starlight", "Cosmic", "Vibrant", "Sleek",
    "Graceful", "Mellow", "Jolly", "Radiant", "Serene", "Brave", "Clever", "Astute", "Merry", "Spunky",
    "Chilly", "Cozy", "Dreamy", "Tiny", "Mega", "Epic", "Magic", "Hyper", "Super", "Mystic",
    "Hidden", "Eldritch", "Phantom", "Savage", "Gothic"
]

NOUNS = [
    "River", "Fox", "Pine", "Stone", "Wave", "Hawk", "Ember", "Creek", "Bear", "Wolf",
    "Eagle", "Falcon", "Owl", "Deer", "Otter", "Badger", "Panda", "Koala", "Tiger", "Lion",
    "Panther", "Leopard", "Cheetah", "Dolphin", "Whale", "Seal", "Rabbit", "Squirrel", "Robin", "Jay",
    "Raven", "Crow", "Heron", "Swan", "Drake", "Phoenix", "Griffin", "Dragon", "Lynx", "Cat",
    "Dog", "Puma", "Jaguar", "Coyote", "Elk", "Moose", "Bison", "Goat", "Ram", "Stag",
    "Ridge", "Peak", "Summit", "Vale", "Glen", "Forest"
]

def generate_display_name() -> str:
    """Generates a random display name by combining an adjective and a noun."""
    return random.choice(ADJECTIVES) + random.choice(NOUNS)

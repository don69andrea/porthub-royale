#!/usr/bin/env python3
"""
Migration script: Create role_references.json from existing asset_roles.json
This estimates reference bboxes based on typical positions for each role.
"""
import json
from pathlib import Path

# Typical positions for each role (estimated from your ROIs and typical use)
ROLE_REFERENCE_ESTIMATES = {
    "GPU_OPERATOR": (900, 640, 950, 720),  # Near nose/GPU area
    "BELT_LOADER": (650, 450, 750, 550),   # Baggage area (average of front/rear)
    "BAGGAGE_HANDLER": (650, 450, 750, 550),  # Near baggage area
    "ENGINE_SAFETY_CREW": (850, 560, 920, 640),  # Engine area (average)
    "STAIRS": (1300, 500, 1400, 600),      # Near passenger door
    "PASSENGER": (1350, 500, 1450, 600),   # Passenger flow area
    "FUEL_TRUCK": (800, 300, 900, 400),    # Fuel area
    "GPU_TRUCK": (900, 640, 1000, 740),    # Nose area
    "PUSHBACK_TUG": (900, 750, 1000, 850), # Pushback area
    "WHEEL_CHOCKS_CREW": (890, 710, 950, 770),  # Wheel chocks area
}

def migrate_tags():
    """Create role_references.json from asset_roles.json"""
    asset_roles_path = Path("data/asset_roles.json")
    role_refs_path = Path("data/role_references.json")

    if not asset_roles_path.exists():
        print("❌ No asset_roles.json found - nothing to migrate")
        return

    if role_refs_path.exists():
        print("⚠️  role_references.json already exists - skipping migration")
        print("   Delete it first if you want to regenerate")
        return

    # Load existing tags
    with open(asset_roles_path, "r") as f:
        asset_roles = json.load(f)

    # Extract unique roles
    roles = set()
    for role in asset_roles.values():
        if role and role != "UNASSIGNED":
            roles.add(role)

    print(f"Found {len(roles)} unique roles in asset_roles.json:")
    for role in sorted(roles):
        print(f"  - {role}")

    # Create reference bboxes
    role_references = {}
    for role in roles:
        if role in ROLE_REFERENCE_ESTIMATES:
            role_references[role] = list(ROLE_REFERENCE_ESTIMATES[role])
            print(f"✓ Added reference bbox for {role}")
        else:
            print(f"⚠️  No reference bbox estimate for {role} - will need manual tagging once")

    # Save role_references.json
    if role_references:
        with open(role_refs_path, "w") as f:
            json.dump(role_references, f, indent=2)
        print(f"\n✅ Created {role_refs_path} with {len(role_references)} reference bboxes")
        print("   Tag recovery will now work on app restart!")
    else:
        print("\n❌ No reference bboxes created - no matching roles found")

if __name__ == "__main__":
    migrate_tags()

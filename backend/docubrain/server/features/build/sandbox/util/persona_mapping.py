"""Persona mapping utility for demo user identities and org structure.

Maps frontend persona selections (work_area + level) to demo user profiles
with name and email for sandbox provisioning.

Also provides organizational structure data and content generators for org_info files.
Single source of truth for both local and Kubernetes sandbox provisioning.
"""

from typing import TypedDict


class PersonaInfo(TypedDict):
    """Type for persona information."""

    name: str
    email: str


# Persona mapping: work_area -> level -> PersonaInfo
PERSONA_MAPPING: dict[str, dict[str, PersonaInfo]] = {
    "engineering": {
        "ic": {
            "name": "Jiwon Kang",
            "email": "jiwon_kang@netherite-extraction.docubrain.app",
        },
        "manager": {
            "name": "Javier Morales",
            "email": "javier_morales@netherite-extraction.docubrain.app",
        },
    },
    "sales": {
        "ic": {
            "name": "Megan Foster",
            "email": "megan_foster@netherite-extraction.docubrain.app",
        },
        "manager": {
            "name": "Valeria Cruz",
            "email": "valeria_cruz@netherite-extraction.docubrain.app",
        },
    },
    "product": {
        "ic": {
            "name": "Michael Anderson",
            "email": "michael_anderson@netherite-extraction.docubrain.app",
        },
        "manager": {
            "name": "David Liu",
            "email": "david_liu@netherite-extraction.docubrain.app",
        },
    },
    "marketing": {
        "ic": {
            "name": "Rahul Patel",
            "email": "rahul_patel@netherite-extraction.docubrain.app",
        },
        "manager": {
            "name": "Olivia Reed",
            "email": "olivia_reed@netherite-extraction.docubrain.app",
        },
    },
    "executives": {
        "ic": {
            "name": "Sarah Mitchell",
            "email": "sarah_mitchell@netherite-extraction.docubrain.app",
        },
        "manager": {
            "name": "Sarah Mitchell",
            "email": "sarah_mitchell@netherite-extraction.docubrain.app",
        },
    },
    "other": {
        "manager": {
            "name": "Ralf Schroeder",
            "email": "ralf_schroeder@netherite-extraction.docubrain.app",
        },
        "ic": {
            "name": "John Carpenter",
            "email": "john_carpenter@netherite-extraction.docubrain.app",
        },
    },
}

# Organization structure - maps managers to their direct reports
ORGANIZATION_STRUCTURE: dict[str, dict[str, list[str]]] = {
    "engineering": {
        "javier_morales@netherite-extraction.docubrain.app": [
            "tyler_jenkins@netherite-extraction.docubrain.app",
            "jiwon_kang@netherite-extraction.docubrain.app",
            "brooke_spencer@netherite-extraction.docubrain.app",
            "andre_robinson@netherite-extraction.docubrain.app",
        ],
        "isabella_torres@netherite-extraction.docubrain.app": [
            "ryan_murphy@netherite-extraction.docubrain.app",
            "jason_morris@netherite-extraction.docubrain.app",
            "kevin_sullivan@netherite-extraction.docubrain.app",
        ],
    },
    "sales": {
        "valeria_cruz@netherite-extraction.docubrain.app": [
            "megan_foster@netherite-extraction.docubrain.app",
            "mina_park@netherite-extraction.docubrain.app",
            "james_choi@netherite-extraction.docubrain.app",
            "camila_vega@netherite-extraction.docubrain.app",
        ],
        "layla_farah@netherite-extraction.docubrain.app": [
            "arjun_mehta@netherite-extraction.docubrain.app",
            "sneha_reddy@netherite-extraction.docubrain.app",
            "irene_shen@netherite-extraction.docubrain.app",
        ],
    },
    "product": {
        "david_liu@netherite-extraction.docubrain.app": [
            "michael_anderson@netherite-extraction.docubrain.app",
            "kenji_watanabe@netherite-extraction.docubrain.app",
            "sofia_ramirez@netherite-extraction.docubrain.app",
        ],
    },
    "marketing": {
        "olivia_reed@netherite-extraction.docubrain.app": [
            "rahul_patel@netherite-extraction.docubrain.app",
            "yuna_lee@netherite-extraction.docubrain.app",
            "peter_yamamoto@netherite-extraction.docubrain.app",
        ],
    },
    "executives": {
        "sarah_mitchell@netherite-extraction.docubrain.app": [
            "daniel_hughes@netherite-extraction.docubrain.app",
            "amanda_brooks@netherite-extraction.docubrain.app",
            "ananya_gupta@netherite-extraction.docubrain.app",
        ],
    },
    "other": {
        "ralf_schroeder@netherite-extraction.docubrain.app": [
            "john_carpenter@netherite-extraction.docubrain.app",
        ],
    },
}

# AGENTS.md content for org_info directory
ORG_INFO_AGENTS_MD = """# AGENTS.md

This file provides information about which organizational information sources are available:

There are two files available that provide important information about the user's company and the user themselves.


## User Identity

The file `user_identity_profile.txt` contains the user's profile.

## Organizational Structure

The file `organization_structure.json` contains a json with the organization's groups, managers, and their reports.
"""


def get_persona_info(work_area: str | None, level: str | None) -> PersonaInfo | None:
    """Get persona info from work area and level.

    Args:
        work_area: User's work area (e.g., "engineering", "product", "sales")
        level: User's level (e.g., "ic", "manager")

    Returns:
        PersonaInfo with name and email, or None if no matching persona
    """
    if not work_area:
        return None

    work_area_lower = work_area.lower().strip()
    level_lower = (level or "manager").lower().strip()

    work_area_mapping = PERSONA_MAPPING.get(work_area_lower)
    if not work_area_mapping:
        return None

    return work_area_mapping.get(level_lower)


def generate_user_identity_content(persona: PersonaInfo) -> str:
    """Generate user identity profile content.

    Args:
        persona: PersonaInfo with name and email

    Returns:
        Content for user_identity_profile.txt
    """
    return f"Your name is {persona['name']}. Your email is {persona['email']}. You are working at Netherite Extraction Corp.\n"

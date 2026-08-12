import re
from typing import Dict, List, Any, Tuple, Optional

# Canonical Semantic Entity Taxonomy
CANONICAL_TAXONOMY: Dict[str, Dict[str, Any]] = {
    # 1. Person Domain
    "person.first_name": {
        "label": "First Name",
        "description": "Applicant's given or first name",
        "patterns": [r"first\s*name", r"given\s*name", r"forename"],
        "example": "ALEXANDER"
    },
    "person.middle_name": {
        "label": "Middle Name",
        "description": "Applicant's middle or secondary name",
        "patterns": [r"middle\s*name", r"other\s*name"],
        "example": "THOMAS"
    },
    "person.last_name": {
        "label": "Surname",
        "description": "Applicant's surname or family name",
        "patterns": [r"surname", r"last\s*name", r"family\s*name"],
        "example": "CHAMBERS"
    },
    "person.full_name": {
        "label": "Full Name",
        "description": "Applicant's complete name",
        "patterns": [r"full\s*name", r"applicant\s*name", r"customer\s*name", r"^name$"],
        "example": "ALEXANDER THOMAS CHAMBERS"
    },
    "person.title": {
        "label": "Title",
        "description": "Honorific title (Mr, Mrs, Ms, Dr)",
        "patterns": [r"title", r"mr\b", r"mrs\b", r"ms\b", r"dr\b"],
        "example": "Mr."
    },
    "person.dob": {
        "label": "Date of Birth",
        "description": "Applicant's birth date (DD/MM/YYYY)",
        "patterns": [r"date\s*of\s*birth", r"birth\s*date", r"dob\b", r"day:\s*month"],
        "example": "14/05/1992"
    },
    "person.gender": {
        "label": "Gender / Sex",
        "description": "Sex or gender designation",
        "patterns": [r"sex\b", r"gender\b", r"male\b", r"female\b"],
        "example": "Male"
    },
    "person.marital_status": {
        "label": "Marital Status",
        "description": "Marital status (Married, Single, Divorced, Widowed)",
        "patterns": [r"marital\s*status", r"married\b", r"single\b", r"divorced\b", r"widowed\b"],
        "example": "Single"
    },
    "person.nationality": {
        "label": "Nationality",
        "description": "Citizenship or nationality",
        "patterns": [r"nationality", r"citizenship", r"country\s*of\s*origin"],
        "example": "Malawian"
    },
    "person.profession": {
        "label": "Profession / Occupation",
        "description": "Profession, occupation, or employment title",
        "patterns": [r"profession", r"occupation", r"employment", r"job\s*title"],
        "example": "Principal Software Architect"
    },
    "person.mother_maiden_name": {
        "label": "Mother's Maiden Name",
        "description": "Mother's maiden name security answer",
        "patterns": [r"mother.*maiden", r"maiden\s*name"],
        "example": "MCDONALD"
    },

    # 2. Contact Domain
    "contact.email": {
        "label": "E-mail Address",
        "description": "Primary electronic mail address",
        "patterns": [r"e-?mail", r"email\s*address"],
        "example": "alex.chambers@tracer.ai"
    },
    "contact.phone_mobile": {
        "label": "Cell / Mobile Phone",
        "description": "Mobile telephone number",
        "patterns": [r"cell\s*no", r"mobile\s*no", r"cellphone", r"phone\s*no"],
        "example": "+265 999 123 456"
    },
    "contact.phone_work": {
        "label": "Work Tel No",
        "description": "Office or work telephone number",
        "patterns": [r"work\s*tel", r"office\s*phone", r"work\s*phone"],
        "example": "+265 1 772 300"
    },
    "contact.address_home": {
        "label": "Home Address",
        "description": "Residential home street address",
        "patterns": [r"home\s*address", r"residential\s*address"],
        "example": "Plot 14/205, Area 10, Lilongwe, Malawi"
    },
    "contact.address_work": {
        "label": "Work Address",
        "description": "Business or employer street address",
        "patterns": [r"work\s*address", r"employer\s*address", r"business\s*address"],
        "example": "City Centre Financial Park, Suite 4B, Lilongwe"
    },
    "contact.village_town": {
        "label": "Village / Hometown",
        "description": "Name of home village or ancestral town",
        "patterns": [r"village", r"hometown", r"home\s*town"],
        "example": "Mzimba"
    },

    # 3. Banking Domain
    "banking.service_centre": {
        "label": "Service Centre / Branch",
        "description": "Bank branch or service centre location",
        "patterns": [r"service\s*centre", r"branch\s*name", r"branch"],
        "example": "Capital City Service Centre"
    },
    "banking.account_number_current": {
        "label": "Current Account No",
        "description": "Primary checking or current account number",
        "patterns": [r"current\s*account", r"checking\s*account"],
        "example": "1002938475"
    },
    "banking.account_number_savings": {
        "label": "Savings Account No",
        "description": "Savings or deposit account number",
        "patterns": [r"savings\s*account", r"deposit\s*account"],
        "example": "2009847361"
    },
    "banking.account_number_nbm": {
        "label": "NBM Account No",
        "description": "General bank account number",
        "patterns": [r"nbm\s*account", r"account\s*no", r"account\s*number"],
        "example": "1002938475"
    },
    "banking.bvn": {
        "label": "BVN",
        "description": "Bank Verification Number (11 digits)",
        "patterns": [r"bvn\b", r"bank\s*verification"],
        "example": "22109847361"
    },
    "banking.nin": {
        "label": "NIN / National ID",
        "description": "National Identity Number",
        "patterns": [r"nin\b", r"national\s*id", r"identity\s*no"],
        "example": "MW920514X88"
    },
    "banking.card_type": {
        "label": "Debit / Credit Card Type",
        "description": "Card product tier (Classic, Gold, Platinum)",
        "patterns": [r"classic\b", r"gold\b", r"platinum\b", r"card\s*type"],
        "example": "Classic"
    }
}

# Standard Preset Profiles for Agentic AI Fill
SAMPLE_PROFILES: Dict[str, Dict[str, Any]] = {
    "individual_retail": {
        "name": "Alexander Chambers (Individual Retail Applicant)",
        "data": {
            "person.first_name": "ALEXANDER",
            "person.middle_name": "THOMAS",
            "person.last_name": "CHAMBERS",
            "person.full_name": "ALEXANDER THOMAS CHAMBERS",
            "person.title": "Mr.",
            "person.dob": "14/05/1992",
            "person.gender": "Male",
            "person.marital_status": "Single",
            "person.nationality": "Malawian",
            "person.profession": "Principal Software Architect",
            "person.mother_maiden_name": "MCDONALD",
            "contact.email": "alex.chambers@tracer.ai",
            "contact.phone_mobile": "+265 999 123 456",
            "contact.phone_work": "+265 1 772 300",
            "contact.address_home": "Plot 14/205, Area 10, Lilongwe",
            "contact.address_work": "City Centre Financial Park, Lilongwe",
            "contact.village_town": "Mzimba",
            "banking.service_centre": "Capital City Service Centre",
            "banking.account_number_current": "1002938475102",
            "banking.account_number_savings": "2009847361904",
            "banking.account_number_nbm": "1002938475102",
            "banking.bvn": "22109847361",
            "banking.nin": "MW920514X88",
            "banking.card_type": "Classic"
        }
    },
    "diaspora_applicant": {
        "name": "Dr. Sarah Banda (Diaspora Account Opening Profile)",
        "data": {
            "person.first_name": "SARAH",
            "person.middle_name": "GRACE",
            "person.last_name": "BANDA",
            "person.full_name": "DR. SARAH GRACE BANDA",
            "person.title": "Dr.",
            "person.dob": "22/11/1986",
            "person.gender": "Female",
            "person.marital_status": "Married",
            "person.nationality": "Malawian",
            "person.profession": "Consultant Cardiologist",
            "person.mother_maiden_name": "PHIRI",
            "contact.email": "sarah.banda@diaspora-med.org",
            "contact.phone_mobile": "+44 7700 900123",
            "contact.phone_work": "+44 20 7946 0912",
            "contact.address_home": "42 Kensington Park Gardens, London, UK",
            "contact.address_work": "St Thomas Hospital, Westminster, London",
            "contact.village_town": "Blantyre",
            "banking.service_centre": "Victoria Avenue Branch",
            "banking.account_number_current": "1056789012345",
            "banking.account_number_savings": "2056789012345",
            "banking.account_number_nbm": "1056789012345",
            "banking.bvn": "22890123456",
            "banking.nin": "MW861122Y99",
            "banking.card_type": "Gold"
        }
    }
}

class TracerSemanticMapper:
    """
    Classifier engine mapping form slot labels and text context to canonical entity URIs.
    """

    def __init__(self):
        self.taxonomy = CANONICAL_TAXONOMY

    def classify_slot(self, slot: Dict[str, Any]) -> Tuple[Optional[str], float]:
        """
        Takes a form slot dict and returns (semantic_uri, confidence_score).
        """
        label = (slot.get("label") or "").strip()
        field_name = (slot.get("field_name") or "").strip()
        combined_text = f"{label} {field_name}".lower()

        if not combined_text.strip():
            return None, 0.0

        best_uri = None
        highest_score = 0.0

        for uri, meta in self.taxonomy.items():
            for pat in meta["patterns"]:
                if re.search(pat, combined_text, re.IGNORECASE):
                    # Direct regex match score
                    score = 0.95 if re.search(r"\b" + pat + r"\b", combined_text, re.IGNORECASE) else 0.80
                    if score > highest_score:
                        highest_score = score
                        best_uri = uri

        return best_uri, round(highest_score, 2)


class TracerAgenticFillEngine:
    """
    Agentic AI Form Filling Engine.
    Takes document slots and an Applicant Profile dictionary, matches semantic URIs,
    and populates line baselines, comb box centroids, and checkbox primitives automatically.
    """

    def __init__(self):
        self.mapper = TracerSemanticMapper()

    def fill_slots_agentically(self, slots_data: Dict[str, List[Dict[str, Any]]], profile_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Maps applicant profile values to document slots matching semantic URIs.
        """
        filled_count = 0

        for page_num_str, slots in slots_data.items():
            processed_groups = set()
            for slot in slots:
                # Classify slot if semantic_uri not set
                semantic_uri = slot.get("semantic_uri")
                if not semantic_uri:
                    semantic_uri, conf = self.mapper.classify_slot(slot)
                    slot["semantic_uri"] = semantic_uri
                    slot["semantic_confidence"] = conf

                if semantic_uri and semantic_uri in profile_data:
                    val = str(profile_data[semantic_uri]).strip()
                    slot_type = slot.get("slot_type")

                    group_id = slot.get("group_id")
                    group_role = slot.get("group_role")
                    if group_id and group_id not in processed_groups and group_role == "character_sequence":
                        group = sorted(
                            [item for item in slots if item.get("group_id") == group_id],
                            key=lambda item: item.get("group_index", 0),
                        )
                        clean_val = re.sub(r"[\s-]+", "", val) if len(val) > len(group) else val
                        for index, item in enumerate(group):
                            item["value"] = clean_val[index] if index < len(clean_val) else ""
                            if item["value"]:
                                filled_count += 1
                        processed_groups.add(group_id)
                        continue
                    if group_id and group_id in processed_groups:
                        continue
                    if group_id and group_role == "date_parts":
                        digits = re.findall(r"\d+", val)
                        day, month, year = (digits + ["", "", ""])[:3]
                        values = {"day": day, "month": month, "year": year}
                        group = [item for item in slots if item.get("group_id") == group_id]
                        for item in group:
                            item["value"] = values.get(item.get("date_part"), "")
                            if item["value"]:
                                filled_count += 1
                        processed_groups.add(group_id)
                        continue

                    if slot_type == "line":
                        slot["value"] = val
                        filled_count += 1
                    elif slot_type == "comb_box":
                        # Format string to remove spaces/hyphens for comb boxes if numeric
                        clean_val = val.replace(" ", "").replace("-", "") if semantic_uri.startswith("banking.") else val
                        slot["value"] = clean_val
                        filled_count += 1
                    elif slot_type == "checkbox":
                        # Check if label matches profile value (e.g. Sex: Male [x])
                        lbl = (slot.get("label") or "").lower()
                        val_low = val.lower()
                        if val_low in ["true", "yes", "1"] or val_low in lbl or lbl in val_low:
                            slot["checked"] = True
                            slot["value"] = "true"
                            filled_count += 1

        return slots_data

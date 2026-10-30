"""
PDF Renamer Tool - Private for Owner Only
Replaces extractor name before .pdf with 卄卂匚K乇尺 (HACKER)

Example:
LECTURE-6 FUNDAMENTAL RIGHTS (Class PDF) POLITY Vivid.pdf
-> LECTURE-6 FUNDAMENTAL RIGHTS (Class PDF) POLITY 卄卂匚K乇尺.pdf

If file is "Vivid.pdf" -> "卄卂匚K乇尺.pdf"
If "Some Name.pdf" -> "Some 卄卂匚K乇尺.pdf" or replaces last word before .pdf

Logic:
- If filename contains "Vivid" (case insensitive), replace it with HACKER
- Else, replace last word before .pdf with HACKER
- Keeps extension .pdf
- Works for batch of 10 PDFs
"""

import os
import re

HACKER_NAME = "卄卂匚K乇尺"
OWNER_ID = int(os.getenv("ADMIN_ID", "0") or 0)

def rename_pdf_filename(original_name: str) -> str:
    """
    Rename PDF filename by replacing extractor name with HACKER
    
    Examples:
    - "LECTURE-6 FUNDAMENTAL RIGHTS (Class PDF) POLITY Vivid.pdf" -> "LECTURE-6 FUNDAMENTAL RIGHTS (Class PDF) POLITY 卄卂匚K乇尺.pdf"
    - "Vivid.pdf" -> "卄卂匚K乇尺.pdf"
    - "My Notes by John.pdf" -> "My Notes by 卄卂匚K乇尺.pdf" (replaces last word before .pdf)
    - "POLITY Vivid.pdf" -> "POLITY 卄卂匚K乇尺.pdf"
    """
    if not original_name:
        return f"{HACKER_NAME}.pdf"
    
    # Ensure .pdf extension
    name_lower = original_name.lower()
    if not name_lower.endswith('.pdf'):
        # If not pdf, add .pdf
        base = original_name
        ext = ".pdf"
    else:
        base = original_name[:-4]  # Remove .pdf
        ext = ".pdf"
    
    # Clean base
    base = base.strip()
    
    # Pattern 1: If contains "Vivid" (case insensitive), replace it
    # Common extractor names: VIVID, Extracted by VIVID, etc.
    # Replace whole word Vivid with HACKER
    if re.search(r'vivid', base, re.IGNORECASE):
        new_base = re.sub(r'vivid', HACKER_NAME, base, flags=re.IGNORECASE)
        # Also clean up "EXTRACTED BY" etc if present? Keep it, just replace Vivid
        return f"{new_base}{ext}"
    
    # Pattern 2: If contains "EXTRACTED BY : XYZ" or similar, replace XYZ
    # Example: "LECTURE-6... EXTRACTED BY VIVID" -> keep but replace VIVID part already handled
    # For generic, replace last word if it looks like a name (capitalized, 3+ chars)
    
    # Split by spaces and replace last token if it's a name-like word
    # But better: replace everything after last occurrence of common separators?
    # Simplest: If filename has pattern "something Name.pdf", replace Name with HACKER
    # We do: find last word (alphanumeric) before .pdf and replace with HACKER
    
    # Remove trailing spaces and common separators
    # Look for pattern: last word is 3-20 chars, letters only, maybe the extractor name
    # Example: "POLITY Vivid" -> last word Vivid -> replace
    # "LECTURE-6 FUNDAMENTAL RIGHTS (Class PDF) POLITY Vivid" -> last word Vivid
    
    # Try to replace last word
    # Split base by spaces
    parts = base.split()
    if len(parts) >= 1:
        last_word = parts[-1]
        # If last word is like a name (not containing special chars like ), ], etc, and length 2-20)
        # And not a common word like PDF, Class, etc.
        common_skip = {'pdf', 'class', 'lecture', 'polity', 'batch', 'notes', 'book', 'module'}
        if last_word.lower() not in common_skip and len(last_word) >= 2 and len(last_word) <= 30:
            # Replace last word with HACKER
            parts[-1] = HACKER_NAME
            new_base = " ".join(parts)
            return f"{new_base}{ext}"
    
    # Fallback: Append HACKER before .pdf
    # If no good replacement found, just add HACKER
    if base:
        return f"{base} {HACKER_NAME}{ext}"
    else:
        return f"{HACKER_NAME}{ext}"

def batch_rename(filenames):
    """Rename list of filenames"""
    result = []
    for fname in filenames:
        new_name = rename_pdf_filename(fname)
        result.append((fname, new_name))
    return result

# Test
if __name__ == "__main__":
    tests = [
        "LECTURE-6 FUNDAMENTAL RIGHTS (Class PDF) POLITY Vivid.pdf",
        "Vivid.pdf",
        "My Notes by John.pdf",
        "POLITY Vivid.pdf",
        "INDIAN POLITY Class Notes.pdf",
        "HISTORY Lecture 5 by VIVID.pdf",
        "EXTRACTED BY VIVID.pdf",
    ]
    for t in tests:
        print(f"{t} -> {rename_pdf_filename(t)}")

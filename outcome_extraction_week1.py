"""
The goal/point of this project is to extract graduate outcome data by parsing pdfs.
To parse the pdfs, we imported the pdfplumber library: https://github.com/jsvine/pdfplumber
Numerous libraries like numpy, pandas, re were imported to handle input and output operations
"""
import os
import re
import pandas as pd
import pdfplumber
import numpy as np


rows = [] # This variable is used to just extract the table row data to later transform into a DataFrame
reports = "GraduationSurveyReports"

"""
The following regex patterns are used to define what a percent looks like in the data 
percent_re matches all numbers with percent sign like 90.0% or <1%
no_percent_re is the same but without % as it accounts for PDFs that split % into a different cell
"""
percent_re = re.compile(r"^<?\d+(?:\.\d+)?%$")
no_percent_re = re.compile(r"^<?\d+(?:\.\d+)?$")

# HELPER FUNCTIONS :)

# Function to convert input to a string and remove any and all whitespaces
def clean(x):
    if x is None:
        return ""
    return str(x).replace("\n", " ").strip()

# Removes commas so numbers like 5,678 to 5678 and checks if input is all digits
def is_count(x):
    x = clean(x).replace(",", "")
    return x.isdigit()

# Remove spaces and detects if input is a percentage
def is_percent(x):
    x = clean(x).replace(" ", "")
    return bool(percent_re.match(x))

# Decides if a cell is likely a label (E.g “Employed FT”, “Unplaced”) or not checking if it has percents, counts, etc
def is_label(s):
    s = clean(s)
    if not s:
        return False
    if is_count(s) or is_percent(s):
        return False

    low = s.lower()

    if low in {"outcome", "#", "%"}:
        return False
    
    # To remove any unwanted label candidates we exclude common titles like "outcome"
    if "reported outcomes" in low or "graduate outcomes" in low:
        return False

    return True

# Collects all cells that look like labels, chooses the longest
def find_label(row):
    labels = [clean(c) for c in row if is_label(c)]
    return max(labels, key=len) if labels else ""


def find_count(row):
    for cell in row:
        if is_count(cell):
            return clean(cell)
    return ""

def find_percent(row):
    row = row or []
    for i, cell in enumerate(row):
        c = clean(cell).replace(" ", "")

        if percent_re.match(c):
            return c

        if no_percent_re.match(c) and i + 1 < len(row):
            nxt = clean(row[i + 1]).strip()
            if nxt == "%":
                return c + "%"

    return ""

# Extracts all text from page, splits into lines and filters to find the proper tile
def get_page_title(page):
    text = page.extract_text() or ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    bad_contains = ( # list of common phrases/words to filter out
        "survey response rate",
        "knowledge rate",
        "total placement",
        "reported outcomes",
        "graduate outcomes",
        "as of",
        "data from",
        "had been collected",
        "via the survey",
        "between",
    )

    def is_good_title(ln: str) -> bool:
        # Checks to see if the given line is a proper title, by filtering noise 
        low = ln.lower().strip()
        
        if low == "maryland":
            return False
        
        if any(b in low for b in bad_contains):
            return False

        if "%" in ln or "#" in ln:
            return False

        if len(ln) > 80:
            return False

        if sum(ch.isdigit() for ch in ln) >= 2 and not re.search(r"university\s*[- ]\s*wide|universitywide", low):
            return False #checks if ln looks too numeric and if it’s not a ‘University-Wide’ label and rejects it
        
        return any(ch.isalpha() for ch in ln)

    # The following block checks the first 25 lines to see if titles are wrapped under multiple lines
    start_idx = None
    for i, ln in enumerate(lines[:25]):
        if is_good_title(ln):
            start_idx = i
            break

    if start_idx is None:
        return ""

    title_lines = [lines[start_idx]]
    for j in range(start_idx + 1, min(start_idx + 4, len(lines))):
        if is_good_title(lines[j]) and len(lines[j]) <= 60:
            title_lines.append(lines[j])
        else:
            break

    return " ".join(title_lines)

# Pages can have multiple tables, this function decides whether its an outcome table
def is_outcomes_table(table):
    score = 0 # uses a score system to decide which table is likely to be an outcome table
    labels = []

    for r in table[:15]:
        if not r:
            continue
        lab = find_label(r).lower()
        cnt = find_count(r)
        pct = find_percent(r)

        if lab:
            labels.append(lab)
        if lab and cnt and pct:
            score += 1

    if score < 3:
        return False, score
    # we use key terms in an outcome table to prevent false positives
    if not any(("employed" in l or "unplaced" in l or "unresolved" in l) for l in labels):
        return False, score

    return True, score

"""
This function is the main heart of the script as this is what parses the table
It uses the previous functions to scrape the data off of the pdf and 
filters out any unwanted inputs
"""
def parse_outcomes_table(table):
    result = []
    last = None # tracks the last successfully-added row (used to concat wrapped labels).
    pending_label = ""

    for r in table:
        if not r:
            continue

        label = find_label(r)
        low = label.lower()
        irrelevant_phrases = [
            "reported outcomes of graduates",
            "reported outcomes of",
            "graduate outcomes",
        ]
        
        # This block is used to filter labels so they are clean
        # (E.g "Employed FT” instead of "Reported Outcomes of Graduates Employed FT”)
        for jp in irrelevant_phrases:
            if jp in low:
                label = re.sub(jp, "", label, flags=re.IGNORECASE).strip()
                low = label.lower()

        label = re.sub(r"\b20\d{2}\s+graduates\b", "", label, flags=re.IGNORECASE).strip()

        label = re.sub(r"\s{2,}", " ", label).strip()
        cnt = find_count(r)
        pct = find_percent(r)

        if label and not cnt and not pct:
            if last is not None:       # Append fragment to prior label since row has no count/percent
                last["outcome"] = (last["outcome"] + " " + label).strip()
            else:
                pending_label = (pending_label + " " + label).strip() # No prior row
            continue

        if not label and pending_label:
            label = pending_label
            pending_label = ""

        if label and pending_label:
            label = (pending_label + " " + label).strip() #Concats multi line labels
            pending_label = ""

        if not label:
            continue
        
        if label.lower() == "outcome": 
            continue
        
        # Both total and not seeking data primarily do not have percents, just counts
        is_total = label.strip().lower() == "total"
        is_not_seeking = label.strip().lower().startswith("not seeking")

        if cnt and (pct or is_total or is_not_seeking):
            row = {"outcome": label, "count": cnt, "percent": pct}
            result.append(row)
            last = row
        else:
            if label and not cnt:
                pending_label = (pending_label + " " + label).strip()

    return result

# Since total and not seeking data is usually different from the rest of the table 
# we search for them using regex 
def get_total_and_not_seeking(page_text):
    found = []

    # Looks for total and a number afterwards
    m_total = re.search(r"\bTOTAL\b\s+([\d,]+)(?:\s+(\d+(\.\d+)?%))?", page_text, re.IGNORECASE)
    if m_total:
        found.append({"outcome": "TOTAL", "count": m_total.group(1), "percent": m_total.group(2) or ""})

    m_ns = re.search(r"\bNot\s+Seeking\b\s+([\d,]+)\b", page_text, re.IGNORECASE)
    if m_ns:
        found.append({"outcome": "Not Seeking", "count": m_ns.group(1), "percent": ""})

    return found

# NOt all labels are consistent throughout each survey, so we standardize them to make extraction easier
def outcome_key(s: str):
    s = (s or "").lower()

    if "employed" in s and "ft" in s: return "Employed FT"
    if "employed" in s and "pt" in s: return "Employed PT"
    if "continuing" in s and "education" in s: return "Continuing Edu"
    if "volunteer" in s or "service program" in s: return "Volunteering"
    if "military" in s: return "Military"
    if "business" in s: return "Business"
    if "unplaced" in s: return "Unplaced"
    if "unresolved" in s: return "Unresolved"
    if re.search(r"\btotal\b", s): return "Total"
    # use regex to prevent weird false matches where “total” appears inside another word
    if "not seeking" in s: return "Not Seeking"
    return None

# Converts percent strings to numbers for math
def pct_to_float(x):
    x = str(x).strip()

    if not x:
        return np.nan
    # For any data that uses <1%, we use 1
    if x.startswith("<") and x.endswith("%"):
        return 1.0

    try:
        return float(x[:-1]) 
    except ValueError:
        return np.nan
 
# Similar to labels, not all units are consistent, so we normalize them for consistency   
def normalize_unit(u: str) -> str:
    u = str(u or "").strip()
    u = re.sub(r"\s+", " ", u)        
    u = u.replace("–", "-").replace("—", "-")

    low = u.lower()
    
    if re.search(r"university\s*[- ]\s*wide|universitywide", low):
        return "University-wide"

    if "university of maryland" in low and (
        "university-wide" in low
        or "university wide" in low
        or "overall" in low
        or "graduate survey report" in low):
        return "University-wide"

    if low == "university of maryland":
        return "University-wide"

    return u

# Processes inputs for simple matching 
# So units like “College of Computer, Mathematical, and Natural Sciences”
# and “College of Computer Mathematical & Natural Sciences” produce similar keys.
def unit_key(s: str) -> str:
    s = str(s or "").lower()
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    toks = [t for t in s.split() if t != "and"]
    return " ".join(toks)

# Use previous functions to normalize and map the inputs to official names
def canonicalize_unit(u: str) -> str:
    u = normalize_unit(u)
    k = unit_key(u)

    if k in unit_lookup:
        return unit_lookup[k]

    for ck, canon in unit_lookup_items: #if exact matching fails, we try something looser with substrings
        if ck in k:
            return canon

    return u

"""
This is the main block of code that runs the script 
that splits and scans PDFs, pages, tables
"""
for file in os.listdir(reports):
    if not file.endswith(".pdf"): #check if file is a pdf
        continue

    new_path = os.path.join(reports, file) # Build filepath by joining folder + filename safely 

    with pdfplumber.open(new_path) as pdf:
        for page in pdf.pages: # Extract pages
            tables = page.extract_tables() or []
            if not tables:
                continue

            page_title = get_page_title(page)
            page_text = page.extract_text() or ""

            candidates = [] # Stores good tables
            for t in tables:
                if not t:
                    continue
                check, score = is_outcomes_table(t)
                if check:
                    candidates.append((score, t))

            if not candidates:
                continue

            candidates.sort(key=lambda x: x[0], reverse=True) #Pick best candidate
            best_table = candidates[0][1]

            parsed = parse_outcomes_table(best_table)
            
            # Add “TOTAL” and “Not Seeking” from page text if missing
            existing_outcomes = {p["outcome"].lower() for p in parsed}
            for extra in get_total_and_not_seeking(page_text):
                if extra["outcome"].lower() not in existing_outcomes:
                    parsed.append(extra)
            # Convert parsed items into rows to convert into dataframe
            for item in parsed:
                rows.append({
                    "pdf": file,
                    "title": page_title,
                    "outcome": item["outcome"],
                    "count": item["count"],
                    "percent": item["percent"],})

df = pd.DataFrame(rows)

df["Year"] = df["pdf"].str.extract(r"(20\d{2})").astype("Int64") #Extract year from pdf filename
df = df.rename(columns={"title": "Unit"})

df["Unit"] = df["Unit"].apply(normalize_unit)
df["Unit"] = (
df["Unit"].astype(str).str.strip() # Extra cleanup after normalization
      .str.replace(r"\s+", " ", regex=True)
      .str.replace("–", "-", regex=False)
      .str.replace("—", "-", regex=False))

unit_order = [ # Order we want our units to be in 
    "University-wide",
    "College of Agriculture and Natural Resources",
    "College of Arts and Humanities",
    "College of Behavioral and Social Sciences",
    "College of Computer, Mathematical, and Natural Sciences",
    "College of Education",
    "College of Information",
    "The A. James Clark School of Engineering",
    "Philip Merrill College of Journalism",
    "School of Architecture, Planning, and Preservation",
    "School of Public Health",
    "School of Public Policy",
    "The Robert H. Smith School of Business",
    "College Park Scholars",
    "Honors College",
    "Letters and Sciences",
    "Undergraduate Studies",
]

# Build lookup table to start matching
unit_lookup = {unit_key(u): u for u in unit_order}

# Clean inconsistinces with typos in several reports
unit_lookup[unit_key("Phillip Merrill College of Journalism")] = "Philip Merrill College of Journalism"
unit_lookup[unit_key("Philip Merrill College of Journalism")]  = "Philip Merrill College of Journalism"

unit_lookup_items = sorted(unit_lookup.items(), key=lambda kv: len(kv[0]), reverse=True)
df["Unit"] = df["Unit"].apply(canonicalize_unit)

df["count"] = df["count"].astype(str).str.replace(",", "", regex=False)
# finds rows where "count" is not a valid whole number and replaces those values with missing data
df.loc[~df["count"].str.fullmatch(r"\d+"), "count"] = np.nan
df["count"] = df["count"].astype("Int64")

df["percent"] = df["percent"].fillna("").astype(str).str.replace(" ", "", regex=False)

df["Outcome"] = df["outcome"].apply(outcome_key)
df = df[df["Outcome"].notna()].copy()

# Next we format the data into wide output table
n = df.pivot_table(index=["Unit", "Year"], columns="Outcome", values="count", aggfunc="first")
p = df.pivot_table(index=["Unit", "Year"], columns="Outcome", values="percent", aggfunc="first")
out = pd.concat([n.add_suffix(" N"), p.add_suffix(" %")], axis=1).reset_index()

# Calculate placement rate data
unplaced = out.get("Unplaced %", "").apply(pct_to_float) if "Unplaced %" in out else np.nan
unresolved = out.get("Unresolved %", "").apply(pct_to_float) if "Unresolved %" in out else np.nan
if "Unplaced %" in out and "Unresolved %" in out:
    out["Placement Rate %"] = (100 - unplaced - unresolved).round(1).astype(str) + "%"

# Sorted order for units
col_order = [
    "Unit","Year",
    "Employed FT N","Employed FT %",
    "Employed PT N","Employed PT %",
    "Continuing Edu N","Continuing Edu %",
    "Volunteering N","Volunteering %",
    "Military N","Military %",
    "Business N","Business %",
    "Unplaced N","Unplaced %",
    "Unresolved N","Unresolved %",
    "Total N",
    "Not Seeking N",
    "Placement Rate %",
]
out = out[[c for c in col_order if c in out.columns]]

all_years = sorted(df["Year"].dropna().astype(int).unique())

# Enforces blank rows for non existent units in previous years
full_index = pd.MultiIndex.from_product([unit_order, all_years], names=["Unit", "Year"])

out = out.set_index(["Unit", "Year"]).reindex(full_index).reset_index() #

# Sort by Year first, then Unit order.
out["Unit_cat"] = pd.Categorical(out["Unit"], categories=unit_order, ordered=True)
out = (
    out.sort_values(["Year", "Unit_cat"], ascending=[True, True])
       .drop(columns=["Unit_cat"])
       .reset_index(drop=True))

# Finally we can export to CSV !!!
out.to_csv("outcome_week1.csv", index=False)
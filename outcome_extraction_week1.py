import os 
import pandas as pd
import pdfplumber
import re


#The goal of this code is to extract outcome reports from survery reports from any year
rows = []
reports = "GraduationSurveyReports"

def clean(x):
    if x is None:
        return ""
    return str(x).replace("\n", " ").strip()
    
def find_count_anywhere(row):
    for cell in row:
        if is_count(cell):
            return clean(cell)
    return ""

def find_percent_anywhere(row):
    for cell in row:
        if is_percent(cell):
            return clean(cell)
    return ""
    
def is_count(x):
    x = clean(x).replace(",", "")
    return x.isdigit()

def find_idx(header_row, target):

    for i, cell in enumerate(header_row):
        c = clean(cell).lower()
        if not c:
            continue
        if target == "outcome" and "outcome" in c:
            return i
        if target == "#" and c == "#":
            return i
        if target == "%" and c == "%":
            return i
    return None

def is_percent(x):
    x = clean(x)
    return bool(re.match(r"^\d+(\.\d+)?%$", x))

def looks_like_label(s):
    s = clean(s)
    if not s:
        return False
    if is_count(s) or is_percent(s):
        return False
    if s.lower() in {"outcome", "#", "%"}:
        return False
    return True

def find_label_anywhere(row):
    labels = [clean(c) for c in row if looks_like_label(c)]
    return max(labels, key=len) if labels else ""

for file in os.listdir(reports):
    if file.endswith(".pdf"):
        new_path = os.path.join(reports, file)
        with pdfplumber.open(new_path) as pdf:
            for page in pdf.pages: 
                tables = page.extract_tables() or []
                for table in tables:
                    if not table or not table[0]:
                        continue
                    header_row_idx = None
                    outcome_idx = count_idx = percent_idx = None
                     # search near top
                    for i, r in enumerate(table[:10]):
                        if not r:
                            continue
                        r_clean = [clean(c).lower() for c in r]
                        
                        if any("outcome" in c for c in r_clean) and "#" in r_clean and "%" in r_clean:
                            header_row_idx = i
                            outcome_idx = find_idx(r, "outcome")
                            count_idx = find_idx(r, "#")
                            percent_idx = find_idx(r, "%")
                            break
                    if header_row_idx is None or outcome_idx is None or count_idx is None or percent_idx is None:
                        # not the Outcomes table (or header not parseable)
                        continue  
                    pending_outcome = ""

                    for r in table[header_row_idx + 1:]:
                        if not r:
                            continue

                        # ensure indexes exist
                        if max(outcome_idx, count_idx, percent_idx) >= len(r):
                            continue

                        outcome = clean(r[outcome_idx])
                        count = clean(r[count_idx])
                        percent = clean(r[percent_idx])
                        
                        if not is_count(count):
                            count = find_count_anywhere(r)
                        if not is_percent(percent):
                            percent = find_percent_anywhere(r)
                            
                        if outcome == "":
                            outcome = find_label_anywhere(r)

                        # Skip obvious junk rows
                        if outcome.lower() in {"outcome"}:
                            continue


                        # Only treat as wrapped text if there are STILL no numbers anywhere
                        if outcome and (count == "" and percent == ""):
                            pending_outcome = (pending_outcome + " " + outcome).strip()
                            continue

                        # If this row has the number pieces and we had a pending wrapped outcome,then we merge them
                        if pending_outcome:
                            if outcome:
                                outcome = (pending_outcome + " " + outcome).strip()
                            else:
                                outcome = pending_outcome
                            pending_outcome = ""
                        if not is_count(count) or outcome == "":
                            continue

                        rows.append({
                            "pdf": file,
                            "outcome": outcome,
                            "count": count,
                            "percent": percent
                        })

df = pd.DataFrame(rows).drop_duplicates()
df.to_csv("outcome_week1.csv", index=False)
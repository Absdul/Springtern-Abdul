import os 
import pandas as pd
import pdfplumber
import re

rows = []
reports = "GraduationSurveyReports"

def clean(x):
    if x is None:
        return ""
    return str(x).replace("\n", " ").strip()
    
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
                    for i, r in enumerate(table[:12]):  # search near top
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
                        continue  # not the Outcomes table (or header not parseable)
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

                        # Skip obvious junk rows
                        if outcome.lower() in {"outcome", "total", "grand total"}:
                            continue

                        # 3) Handle wrapped outcomes:
                        # If outcome text exists but count/percent aren't valid yet, treat as a continuation line
                        if outcome and (not is_count(count) or not is_percent(percent)):
                            pending_outcome = (pending_outcome + " " + outcome).strip()
                            continue

                        # If this row has the numeric pieces and we had a pending wrapped outcome, merge them
                        if pending_outcome:
                            if outcome:
                                outcome = (pending_outcome + " " + outcome).strip()
                            else:
                                outcome = pending_outcome
                            pending_outcome = ""

                        # Keep only real data rows with valid count + percent
                        if not is_count(count) or not is_percent(percent):
                            continue

                        rows.append({
                            "pdf": file,
                            "outcome": outcome,
                            "count": count,
                            "percent": percent
                        })

df = pd.DataFrame(rows).drop_duplicates()
df.to_csv("outcome_week1.csv", index=False)
#!/usr/bin/env python3
"""Replace bundled listings from the supplied Excel workbook using only Python's standard library."""
import argparse
import csv
import json
import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = PROJECT_DIR / "Industrial-Visit-Data-2026-27.xlsx"
LISTINGS_PATH = PROJECT_DIR / "data" / "industrial-listings.json"
FOLLOW_UP_PATH = PROJECT_DIR / "data" / "industrial-listings-follow-up.csv"

NAMESPACES = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "office": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "package": "http://schemas.openxmlformats.org/package/2006/relationships",
}
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
DOMAIN_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?[A-Z0-9-]+(?:\.[A-Z0-9-]+)+(?:/[^\s]*)?$",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"^[+0-9][0-9 \-()]{6,18}$")
PHARMACY_PRODUCT_RE = re.compile(
    r"\b(pharma(?:ceutical|cutical|cutectial|cucital|cetuctial)?s?|"
    r"pharmac(?:ia|eutical|utical|utectial|ucital|etuctial)?s?|medicines?|"
    r"tablets?|capsules?|ointments?|syrups?|drugs?|formulations?|"
    r"ayurvedic|injections?(?!\s*mould)\b)",
    re.IGNORECASE,
)
MEDICINE_PRODUCT_RE = re.compile(
    r"\b(pharmaceuticals?|pharmacuticals?|pharmacutectials?|pharmacucitals?|"
    r"pharmacetuctials?|medicines?|tablets?|capsules?|ointments?|"
    r"syrups?|drugs?|formulations?|injections?(?!\s*mould)\b)",
    re.IGNORECASE,
)
PHARMACY_COMPANY_RE = re.compile(
    r"\b(pharma(?:ceutical|cutical|cutectial|cucital|cetuctial)?s?|"
    r"pharmac(?:ia|eutical|utical|utectial|ucital|etuctial)?s?|"
    r"lifesciences?|life sciences|biotech|biogenetic|formulations?|remedies|"
    r"medicare|drugs?)\b",
    re.IGNORECASE,
)
ANCILLARY_PRODUCT_RE = re.compile(
    r"\b(packaging|labels?|foils?|machines?|machinery|moulds?|moulding|"
    r"spares?|maintenance|bottles?|cartons?)\b",
    re.IGNORECASE,
)


def column_number(cell_reference):
    """Convert a spreadsheet column label such as AB to its one-based number."""
    number = 0
    for character in re.match(r"[A-Z]+", cell_reference).group():
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def read_workbook(workbook_path):
    """Read the first worksheet's displayed text values without third-party packages."""
    try:
        archive = ZipFile(workbook_path)
    except (OSError, BadZipFile) as exc:
        raise ValueError(f"Cannot open Excel workbook: {workbook_path}") from exc

    with archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(part.text or "" for part in item.findall(".//main:t", NAMESPACES))
                for item in shared_root.findall("main:si", NAMESPACES)
            ]

        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        relationship_root = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
        relationships = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationship_root.findall("package:Relationship", NAMESPACES)
        }
        sheets = workbook_root.findall("main:sheets/main:sheet", NAMESPACES)
        sheet = next(
            (item for item in sheets if item.attrib.get("name") == "Industrial Visit Data"),
            None,
        )
        if sheet is None:
            raise ValueError("The workbook does not contain the 'Industrial Visit Data' sheet.")
        relationship_id = sheet.attrib[f"{{{NAMESPACES['office']}}}id"]
        target = relationships[relationship_id]
        sheet_path = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        sheet_root = ET.fromstring(archive.read(sheet_path))

        rows = []
        for row in sheet_root.findall("main:sheetData/main:row", NAMESPACES):
            values = {}
            for cell in row.findall("main:c", NAMESPACES):
                cell_value = cell.find("main:v", NAMESPACES)
                value = cell_value.text if cell_value is not None else ""
                if cell.attrib.get("t") == "s" and value:
                    value = shared_strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(
                        part.text or ""
                        for part in cell.findall(".//main:t", NAMESPACES)
                    )
                values[column_number(cell.attrib["r"])] = (value or "").strip()
            rows.append((int(row.attrib["r"]), values))

    if not rows:
        raise ValueError("The workbook sheet is empty.")
    headers = [value for _, value in sorted(rows[0][1].items())]
    required_headers = {
        "Sr No", "Company Name", "Address", "Product", "Email / Website",
        "Phone 1", "Phone 2", "Phone 3", "Contact Person",
    }
    if not required_headers.issubset(headers):
        missing_headers = ", ".join(sorted(required_headers.difference(headers)))
        raise ValueError(f"The workbook is missing required columns: {missing_headers}.")

    records = []
    for row_number, values in rows[1:]:
        record = {
            header: values.get(index + 1, "")
            for index, header in enumerate(headers)
        }
        if any(record.values()):
            record["_row_number"] = row_number
            records.append(record)
    return records


def normalized_name(name):
    return " ".join(name.casefold().split())


def clean_value(value):
    value = value.strip()
    return "" if value.casefold() in {"", "na", "n/a", "none", "-"} else value


def is_pharmaceutical(company_name, product):
    """Use clear medicine/product terms, not packaging or machinery alone."""
    if MEDICINE_PRODUCT_RE.search(product):
        return True
    if ANCILLARY_PRODUCT_RE.search(product):
        return False
    return bool(
        PHARMACY_COMPANY_RE.search(company_name)
        or PHARMACY_PRODUCT_RE.search(product)
    )


def parse_contacts(raw_contacts):
    """Separate recognized email addresses and websites, leaving unclear text for review."""
    emails = list(dict.fromkeys(EMAIL_RE.findall(raw_contacts)))
    without_emails = EMAIL_RE.sub(" ", raw_contacts)
    websites = []
    unrecognized_parts = []

    for part in re.split(r"[,;\s]+", without_emails):
        part = part.strip("()[]{}<>.,")
        if not part:
            continue
        if DOMAIN_RE.fullmatch(part):
            website = part if re.match(r"^https?://", part, re.IGNORECASE) else f"https://{part}"
            if website not in websites:
                websites.append(website)
        else:
            unrecognized_parts.append(part)

    return emails, websites, " ".join(unrecognized_parts)


def load_existing_listings():
    with LISTINGS_PATH.open(encoding="utf-8") as source_file:
        old_listings = json.load(source_file)
    return {
        normalized_name(item["name"]): item
        for item in old_listings
        if isinstance(item, dict) and item.get("name")
    }


def make_listing(record, old_listings):
    """Build one directory listing and note fields that need a human follow-up."""
    company_name = record["Company Name"].strip()
    if not company_name:
        raise ValueError(f"Company name is missing on spreadsheet row {record['_row_number']}.")

    old_listing = old_listings.get(normalized_name(company_name), {})
    product = clean_value(record["Product"])
    category = (
        "Pharmaceuticals"
        if is_pharmaceutical(company_name, product)
        else old_listing.get("category") or "Manufacturers & factories"
    )
    description = (
        f"Products: {product}"
        if product
        else old_listing.get("description", "")
    )

    valid_phones = []
    invalid_phones = []
    for column in ("Phone 1", "Phone 2", "Phone 3"):
        phone = record[column].strip()
        if not phone:
            continue
        if PHONE_RE.fullmatch(phone):
            valid_phones.append(phone)
        else:
            invalid_phones.append(f"{column} value needs checking: {phone}")

    raw_contacts = clean_value(record["Email / Website"])
    emails, websites, unrecognized_contact = parse_contacts(raw_contacts)
    contact_notes = unrecognized_contact
    if len(emails) > 2 or len(websites) > 1:
        contact_notes = " ".join(part for part in (contact_notes, raw_contacts) if part)

    listing = {
        "name": company_name,
        "category": category,
        "city": old_listing.get("city", ""),
        "description": description,
        "address": clean_value(record["Address"]),
        "phone": valid_phones[0] if valid_phones else None,
        "phone2": valid_phones[1] if len(valid_phones) > 1 else None,
        "phone3": valid_phones[2] if len(valid_phones) > 2 else None,
        "email": emails[0].lower() if emails else "",
        "email2": emails[1].lower() if len(emails) > 1 else "",
        "website": websites[0] if websites else "",
        "contact_notes": contact_notes,
    }

    missing_fields = []
    if not listing["address"]:
        missing_fields.append("Address")
    if not product:
        missing_fields.append("Product")
    if normalized_name(company_name) not in old_listings:
        missing_fields.append(f"Category (assigned; verify: {category})")
    if not listing["city"]:
        missing_fields.append("City (not in workbook)")
    if not valid_phones:
        missing_fields.append("Phone")
    if not emails and not websites:
        missing_fields.append("Email / Website")
    if not clean_value(record["Contact Person"]):
        missing_fields.append("Contact Person")
    if unrecognized_contact:
        missing_fields.append("Email / Website (needs review)")

    follow_up_notes = list(invalid_phones)
    if unrecognized_contact:
        follow_up_notes.append(f"Unparsed contact text: {unrecognized_contact}")
    if len(emails) > 2 or len(websites) > 1:
        follow_up_notes.append("More contact values than the listing fields can display.")

    review_row = None
    if missing_fields or follow_up_notes:
        review_row = {
            "Spreadsheet row": record["_row_number"],
            "Company name": company_name,
            "Fields to follow up": ", ".join(missing_fields),
            "Notes": "; ".join(follow_up_notes),
        }
    return listing, review_row


def main():
    parser = argparse.ArgumentParser(
        description="Replace bundled industrial listings from an Excel workbook."
    )
    parser.add_argument(
        "workbook",
        nargs="?",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help="Excel workbook to import (defaults to the supplied workbook).",
    )
    workbook_path = parser.parse_args().workbook
    records = read_workbook(workbook_path)
    old_listings = load_existing_listings()

    listings = []
    review_rows = []
    names_seen = set()
    for record in records:
        listing, review_row = make_listing(record, old_listings)
        normalized = normalized_name(listing["name"])
        if normalized in names_seen:
            raise ValueError(f"Duplicate company name in workbook: {listing['name']}.")
        names_seen.add(normalized)
        listings.append(listing)
        if review_row:
            review_rows.append(review_row)

    LISTINGS_PATH.write_text(
        json.dumps(listings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with FOLLOW_UP_PATH.open("w", encoding="utf-8", newline="") as review_file:
        writer = csv.DictWriter(
            review_file,
            fieldnames=("Spreadsheet row", "Company name", "Fields to follow up", "Notes"),
        )
        writer.writeheader()
        writer.writerows(review_rows)

    print(
        f"Prepared {len(listings)} listings from {workbook_path}; "
        f"{len(review_rows)} companies need follow-up."
    )
    print(f"Updated {LISTINGS_PATH}")
    print(f"Created {FOLLOW_UP_PATH}")


if __name__ == "__main__":
    main()

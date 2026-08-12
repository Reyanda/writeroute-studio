import sys
import os
import json
import argparse
from pdfstudio.unbundler import TracerUnbundler
from pdfstudio.slot_detector import TracerSlotDetector
from pdfstudio.rebundler import TracerRebundler

def main():
    parser = argparse.ArgumentParser(description="Tracer PDF Precision Form Filling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Unbundle Command
    p_unbundle = subparsers.add_parser("unbundle", help="Unbundle PDF into raw primitives & JSON")
    p_unbundle.add_argument("pdf_path", help="Path to input PDF")
    p_unbundle.add_argument("--output", "-o", help="Output JSON path")

    # 2. Detect Slots Command
    p_detect = subparsers.add_parser("detect", help="Detect form slots (lines, comb boxes, checkboxes)")
    p_detect.add_argument("pdf_path", help="Path to input PDF")
    p_detect.add_argument("--output", "-o", help="Output JSON path")

    # 3. Fill and Rebundle Command
    p_fill = subparsers.add_parser("fill", help="Fill form slots and rebundle PDF")
    p_fill.add_argument("pdf_path", help="Input source PDF")
    p_fill.add_argument("slots_json", help="JSON file containing slot values")
    p_fill.add_argument("output_pdf", help="Destination filled PDF path")

    args = parser.parse_args()

    if args.command == "unbundle":
        unbundler = TracerUnbundler(args.pdf_path)
        data = unbundler.unbundle_document()
        unbundler.close()

        if args.output:
            with open(args.output, "w") as f:
                json.dump(data, f, indent=2)
            print(f"Unbundled data saved to {args.output}")
        else:
            print(json.dumps(data, indent=2))

    elif args.command == "detect":
        unbundler = TracerUnbundler(args.pdf_path)
        doc_data = unbundler.unbundle_document()
        detector = TracerSlotDetector()

        all_slots = {}
        for page_data in doc_data["pages"]:
            p_num = page_data["page_number"]
            slots = detector.detect_slots_for_page(page_data, unbundler.doc, p_num - 1)
            all_slots[p_num] = slots

        unbundler.close()

        output_data = {
            "file_name": os.path.basename(args.pdf_path),
            "total_slots": sum(len(s) for s in all_slots.values()),
            "pages": all_slots
        }

        if args.output:
            with open(args.output, "w") as f:
                json.dump(output_data, f, indent=2)
            print(f"Detected slots saved to {args.output}")
        else:
            print(json.dumps(output_data, indent=2))

    elif args.command == "fill":
        with open(args.slots_json, "r") as f:
            slots_data = json.load(f)

        pages_slots = slots_data.get("pages", slots_data)
        # Convert string page keys to ints if necessary
        formatted_slots = {}
        for k, v in pages_slots.items():
            formatted_slots[int(k)] = v

        rebundler = TracerRebundler(args.pdf_path)
        out_file = rebundler.fill_and_rebundle(formatted_slots, args.output_pdf)
        print(f"Rebundled filled PDF generated at {out_file}")

if __name__ == "__main__":
    main()

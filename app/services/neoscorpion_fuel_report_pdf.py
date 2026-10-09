"""PDF presentation of the canonical Fuel Report context."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def fuel_report_pdf(context):
    output = BytesIO()
    styles = getSampleStyleSheet()
    cell_style = styles["BodyText"]
    cell_style.fontSize = 8
    cell_style.leading = 10
    operation = context["operation"]
    scope = (f"{operation.gateway_code} {operation.sort_name} {operation.sort_date}" if operation else "No current sort")
    summary = context["fuel_report_summary"]
    story = [Paragraph("Fuel Report", styles["Title"]),
             Paragraph(escape(scope), styles["Normal"]), Spacer(1, 10),
             Paragraph(f"Events: {summary['event_count']} | Fuel: {summary['fuel_count']} | Uplift: {summary['uplift_count']} | Defuel: {summary['defuel_count']}", styles["Normal"])]
    totals = context["sort_fuel_totals"]
    story.append(Paragraph(" | ".join(
        f"{label}: {totals[key]['display']} {totals[key]['unit']}"
        for key, label in (("estimated", "TOTAL EST FUEL"), ("transfer", "TOTAL T/F"),
                           ("required", "TOTAL REQUIRED FUEL"))), styles["Normal"]))
    if any(stat["incomplete"] for stat in totals.values()):
        story.append(Paragraph("* Known subtotal; missing values are excluded. A dash means all values are unknown.", styles["Normal"]))
    story.append(Spacer(1, 12))
    headers = ["#", "Type", "Flight / Dest", "Tail", "Fueler", "Truck", "Start", "End", "T/F GAL", "Required", "Neo Fuel"]
    data = [headers]
    for row in context["fuel_report_rows"]:
        gallons = row["transfer_fuel_gallons"]
        values = [row["number"], row["event_type_label"], f"{row['flight_number']} / {row['destination']}", row["tail_number"], row["fueler_name"], row["truck_number"], row["started_time"], row["ended_time"], f"{gallons:,}" if gallons is not None else "-", row["required_fuel_display"], row["neo_fuel_display"]]
        data.append([Paragraph(escape(str(value or "-")), cell_style) for value in values])
    if len(data) > 1:
        table = Table(data, colWidths=[24, 48, 100, 65, 110, 50, 43, 43, 65, 58, 58], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ece8d6")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, 0), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 0), (-1, -1), .3, colors.lightgrey)]))
        story.append(table)
    else:
        story.append(Paragraph("No physical fueling events have been completed for this night yet.", styles["Normal"]))
    SimpleDocTemplate(output, pagesize=landscape(letter), leftMargin=14, rightMargin=14, topMargin=24, bottomMargin=24, title="Fuel Report").build(story)
    output.seek(0)
    return output

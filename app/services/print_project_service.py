from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from app.services.database_service import DatabaseService


class PrintProjectService:
    def __init__(self, database: DatabaseService):
        self.database = database

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def list_projects(self):
        with self.database.connection() as c:
            rows = c.execute(
                """SELECT p.id,p.name,p.default_back_artwork_id,
                          b.filename default_back_filename,
                          b.relative_path default_back_relative_path,
                          p.created_at,p.updated_at,
                          COUNT(i.id) unique_cards,
                          COALESCE(SUM(i.quantity),0) total_cards
                   FROM print_projects p
                   LEFT JOIN print_project_items i ON i.project_id=p.id
                   LEFT JOIN artwork_processed b ON b.id=p.default_back_artwork_id
                   GROUP BY p.id
                   ORDER BY p.name COLLATE NOCASE"""
            ).fetchall()
        return [dict(r) for r in rows]

    def project(self, project_id):
        with self.database.connection() as c:
            row = c.execute(
                """SELECT p.*,b.filename default_back_filename,
                          b.relative_path default_back_relative_path
                   FROM print_projects p
                   LEFT JOIN artwork_processed b ON b.id=p.default_back_artwork_id
                   WHERE p.id=?""",
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_project(self, name):
        name = name.strip()
        if not name:
            raise ValueError("Project name is required")
        now = self._now()
        with self.database.connection() as c:
            return int(c.execute(
                "INSERT INTO print_projects(name,created_at,updated_at) VALUES(?,?,?)",
                (name, now, now),
            ).lastrowid)

    def rename_project(self, project_id, name):
        name = name.strip()
        if not name:
            raise ValueError("Project name is required")
        with self.database.connection() as c:
            c.execute(
                "UPDATE print_projects SET name=?,updated_at=? WHERE id=?",
                (name, self._now(), project_id),
            )

    def duplicate_project(self, project_id, name):
        source = self.project(project_id)
        new_id = self.create_project(name)
        with self.database.connection() as c:
            c.execute(
                "UPDATE print_projects SET default_back_artwork_id=? WHERE id=?",
                (source["default_back_artwork_id"], new_id),
            )
            c.execute(
                """INSERT INTO print_project_items(
                       project_id,processed_artwork_id,back_artwork_id,quantity,sort_order)
                   SELECT ?,processed_artwork_id,back_artwork_id,quantity,sort_order
                   FROM print_project_items WHERE project_id=?""",
                (new_id, project_id),
            )
        return new_id

    def delete_project(self, project_id):
        with self.database.connection() as c:
            c.execute("DELETE FROM print_projects WHERE id=?", (project_id,))

    def set_default_back(self, project_id, artwork_id):
        with self.database.connection() as c:
            c.execute(
                "UPDATE print_projects SET default_back_artwork_id=?,updated_at=? WHERE id=?",
                (artwork_id, self._now(), project_id),
            )

    def set_item_back(self, item_id, artwork_id):
        with self.database.connection() as c:
            c.execute("UPDATE print_project_items SET back_artwork_id=? WHERE id=?", (artwork_id, item_id))

    def available_artwork(self, query=""):
        params = []
        where = "WHERE readable=1"
        if query.strip():
            where += " AND (filename LIKE ? COLLATE NOCASE OR relative_path LIKE ? COLLATE NOCASE)"
            value = f"%{query.strip()}%"
            params = [value, value]
        with self.database.connection() as c:
            rows = c.execute(
                f"""SELECT id,filename,relative_path,stem,width,height,
                           file_size,modified_ns,'READY' status
                    FROM artwork_processed {where}
                    ORDER BY filename COLLATE NOCASE""",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def items(self, project_id):
        with self.database.connection() as c:
            rows = c.execute(
                """SELECT i.id,i.project_id,i.processed_artwork_id,i.back_artwork_id,
                          i.quantity,i.sort_order,p.filename,p.relative_path,
                          p.width,p.height,p.file_size,p.modified_ns,
                          cb.filename custom_back_filename,
                          cb.relative_path custom_back_relative_path,
                          pr.default_back_artwork_id,
                          db.filename default_back_filename,
                          db.relative_path default_back_relative_path,
                          CASE
                            WHEN i.back_artwork_id IS NOT NULL THEN 'Custom Back'
                            WHEN pr.default_back_artwork_id IS NOT NULL THEN 'Default Back'
                            ELSE 'Missing Back'
                          END back_status,
                          COALESCE(cb.filename,db.filename) effective_back_filename,
                          COALESCE(cb.relative_path,db.relative_path) effective_back_relative_path,
                          'READY' status
                   FROM print_project_items i
                   JOIN artwork_processed p ON p.id=i.processed_artwork_id
                   JOIN print_projects pr ON pr.id=i.project_id
                   LEFT JOIN artwork_processed cb ON cb.id=i.back_artwork_id
                   LEFT JOIN artwork_processed db ON db.id=pr.default_back_artwork_id
                   WHERE i.project_id=? ORDER BY i.sort_order,i.id""",
                (project_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_artwork(self, project_id, ids):
        with self.database.connection() as c:
            start = c.execute(
                "SELECT COALESCE(MAX(sort_order),-1)+1 FROM print_project_items WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            for offset, artwork_id in enumerate(ids):
                c.execute(
                    """INSERT INTO print_project_items(project_id,processed_artwork_id,quantity,sort_order)
                       VALUES(?,?,1,?)
                       ON CONFLICT(project_id,processed_artwork_id)
                       DO UPDATE SET quantity=quantity+1""",
                    (project_id, artwork_id, start + offset),
                )
            c.execute("UPDATE print_projects SET updated_at=? WHERE id=?", (self._now(), project_id))

    def remove_item(self, item_id):
        with self.database.connection() as c:
            c.execute("DELETE FROM print_project_items WHERE id=?", (item_id,))

    def set_quantity(self, item_id, quantity):
        if quantity < 1:
            raise ValueError("Quantity must be at least 1")
        with self.database.connection() as c:
            c.execute("UPDATE print_project_items SET quantity=? WHERE id=?", (quantity, item_id))

    def move_item(self, project_id, item_id, direction):
        ids = [item["id"] for item in self.items(project_id)]
        if item_id not in ids:
            return
        old = ids.index(item_id)
        new = max(0, min(len(ids)-1, old+direction))
        if old == new:
            return
        ids.insert(new, ids.pop(old))
        with self.database.connection() as c:
            c.executemany("UPDATE print_project_items SET sort_order=? WHERE id=?", [(n,i) for n,i in enumerate(ids)])

    def export_csv(self, project_id, path: Path):
        rows = self.items(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow([
                "Order","Filename","Relative Path","Quantity","Resolution","Status",
                "Back Filename","Back Relative Path","Back Status",
            ])
            for number, row in enumerate(rows, 1):
                writer.writerow([
                    number,row["filename"],row["relative_path"],row["quantity"],
                    f"{row['width']}x{row['height']}",row["status"],
                    row["effective_back_filename"] or "",
                    row["effective_back_relative_path"] or "",
                    row["back_status"],
                ])

    @staticmethod
    def _normalized_headers(fieldnames):
        aliases = {
            "filename":"filename","file name":"filename","name":"filename",
            "relative path":"relative_path","relative_path":"relative_path","path":"relative_path",
            "quantity":"quantity","qty":"quantity","copies":"quantity","count":"quantity",
            "order":"order","sort order":"order","sort_order":"order",
            "back filename":"back_filename","back file name":"back_filename","back_filename":"back_filename",
            "back relative path":"back_relative_path","back_relative_path":"back_relative_path",
            "back path":"back_relative_path",
        }
        result = {}
        for original in fieldnames or []:
            normalized = " ".join(str(original).strip().lower().replace("_", " ").split())
            canonical = aliases.get(normalized)
            if canonical:
                result[canonical] = original
        return result

    @staticmethod
    def _build_indexes(artwork):
        by_path, by_filename, by_stem = {}, {}, {}
        for record in artwork:
            rel = str(record["relative_path"]).replace("\\", "/").casefold()
            filename = str(record["filename"]).casefold()
            stem = Path(str(record["filename"])).stem.casefold()
            by_path.setdefault(rel, []).append(record)
            by_filename.setdefault(filename, []).append(record)
            by_stem.setdefault(stem, []).append(record)
        return by_path, by_filename, by_stem

    @staticmethod
    def _match(filename, relative_path, indexes):
        by_path, by_filename, by_stem = indexes
        candidates, method = [], ""
        if relative_path:
            candidates = by_path.get(relative_path.replace("\\", "/").casefold(), [])
            method = "Relative Path"
        if not candidates and filename:
            candidates = by_filename.get(filename.casefold(), [])
            method = "Filename"
        if not candidates and filename:
            candidates = by_stem.get(Path(filename).stem.casefold(), [])
            method = "Filename stem"
        return candidates, method

    def analyze_csv(self, path: Path):
        path = Path(path)
        with path.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            headers = self._normalized_headers(reader.fieldnames)
            if "filename" not in headers and "relative_path" not in headers:
                raise ValueError("CSV must contain a Filename column, a Relative Path column, or both.")
            source_rows = list(reader)

        indexes = self._build_indexes(self.available_artwork())
        analyzed = []
        for source_index, source in enumerate(source_rows, 1):
            filename = str(source.get(headers.get("filename", ""), "") or "").strip()
            relative_path = str(source.get(headers.get("relative_path", ""), "") or "").strip().replace("\\", "/")
            back_filename = str(source.get(headers.get("back_filename", ""), "") or "").strip()
            back_relative_path = str(source.get(headers.get("back_relative_path", ""), "") or "").strip().replace("\\", "/")
            quantity_text = str(source.get(headers.get("quantity", ""), "1") or "1").strip()
            order_text = str(source.get(headers.get("order", ""), source_index) or source_index).strip()

            try:
                quantity = int(quantity_text)
                if quantity < 1: raise ValueError
            except ValueError:
                analyzed.append({"source_row":source_index,"filename":filename,"relative_path":relative_path,
                    "back_filename":back_filename,"back_relative_path":back_relative_path,
                    "quantity":quantity_text,"order":source_index,"status":"Invalid quantity",
                    "message":"Quantity must be a positive whole number.","artwork_id":None,"back_artwork_id":None,
                    "back_status":"Not checked"})
                continue
            try: order = int(order_text)
            except ValueError: order = source_index

            candidates, method = self._match(filename, relative_path, indexes)
            if len(candidates) != 1:
                status = "Ambiguous" if len(candidates)>1 else "Unmatched"
                message = f"{len(candidates)} processed files match this row." if candidates else "No indexed processed artwork matched this row."
                analyzed.append({"source_row":source_index,"filename":filename,"relative_path":relative_path,
                    "back_filename":back_filename,"back_relative_path":back_relative_path,"quantity":quantity,"order":order,
                    "status":status,"message":message,"artwork_id":None,"back_artwork_id":None,"back_status":"Not checked"})
                continue

            front = candidates[0]
            back_id = None
            back_status = "Default Back"
            back_message = "Uses the project default back."
            matched_back_filename = ""
            matched_back_relative_path = ""
            if back_filename or back_relative_path:
                backs, back_method = self._match(back_filename, back_relative_path, indexes)
                if len(backs) == 1:
                    back = backs[0]
                    back_id = int(back["id"])
                    back_status = "Custom Back"
                    back_message = f"Back matched by {back_method}."
                    matched_back_filename = back["filename"]
                    matched_back_relative_path = back["relative_path"]
                elif len(backs) > 1:
                    back_status = "Ambiguous Back"
                    back_message = f"{len(backs)} processed files match this back."
                else:
                    back_status = "Missing Back"
                    back_message = "No indexed processed artwork matched this back."

            analyzed.append({"source_row":source_index,"filename":filename or front["filename"],
                "relative_path":relative_path or front["relative_path"],"quantity":quantity,"order":order,
                "status":"Matched","message":f"Matched by {method}.","artwork_id":int(front["id"]),
                "matched_filename":front["filename"],"matched_relative_path":front["relative_path"],
                "back_filename":back_filename,"back_relative_path":back_relative_path,"back_artwork_id":back_id,
                "back_status":back_status,"back_message":back_message,
                "matched_back_filename":matched_back_filename,"matched_back_relative_path":matched_back_relative_path})

        return {"path":str(path),"suggested_name":path.stem,"rows":analyzed,
            "matched":sum(r["status"]=="Matched" for r in analyzed),
            "unmatched":sum(r["status"]=="Unmatched" for r in analyzed),
            "ambiguous":sum(r["status"]=="Ambiguous" for r in analyzed),
            "invalid":sum(r["status"].startswith("Invalid") for r in analyzed),
            "custom_backs":sum(r.get("back_status")=="Custom Back" for r in analyzed),
            "back_warnings":sum(r.get("back_status") in {"Missing Back","Ambiguous Back"} for r in analyzed),
            "total":len(analyzed)}

    def create_project_from_csv(self, analysis, project_name):
        matched_rows = [r for r in analysis["rows"] if r.get("status")=="Matched"]
        if not matched_rows:
            raise ValueError("The CSV has no matched artwork rows to import.")
        project_id = self.create_project(project_name)
        try:
            combined, sequence = {}, []
            for row in sorted(matched_rows, key=lambda x:(int(x.get("order",0)),int(x["source_row"]))):
                artwork_id = int(row["artwork_id"])
                back_id = row.get("back_artwork_id")
                if artwork_id not in combined:
                    combined[artwork_id] = {"quantity":int(row["quantity"]),"back_id":back_id}
                    sequence.append(artwork_id)
                else:
                    combined[artwork_id]["quantity"] += int(row["quantity"])
                    if combined[artwork_id]["back_id"] is None and back_id is not None:
                        combined[artwork_id]["back_id"] = back_id
            with self.database.connection() as c:
                c.executemany(
                    """INSERT INTO print_project_items(project_id,processed_artwork_id,back_artwork_id,quantity,sort_order)
                       VALUES(?,?,?,?,?)""",
                    [(project_id,aid,combined[aid]["back_id"],combined[aid]["quantity"],n) for n,aid in enumerate(sequence)],
                )
        except Exception:
            self.delete_project(project_id)
            raise
        return project_id

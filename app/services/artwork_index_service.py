from __future__ import annotations
import hashlib, time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image
from app.models.artwork_record import ArtworkRecord
from app.models.index_results import ArtworkIndexProgress, ArtworkIndexSummary, ArtworkSearchResult
from app.services.database_service import DatabaseService

SUPPORTED_EXTENSIONS={'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}
ProgressCallback=Callable[[ArtworkIndexProgress],None]

class ArtworkIndexService:
    def __init__(self,database:DatabaseService): self.database=database

    def index_folder(self, root:Path, progress_callback:ProgressCallback|None=None, library_kind:str='original')->ArtworkIndexSummary:
        root=Path(root).resolve(); table=self._table(library_kind)
        if not root.is_dir(): raise FileNotFoundError(f"Artwork folder does not exist: {root}")
        started=time.perf_counter(); files=sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
        summary=ArtworkIndexSummary(discovered=len(files),database_path=str(self.database.database_path))
        with self.database.connection() as c:
            existing={r['relative_path']:(r['file_size'],r['modified_ns']) for r in c.execute(f"SELECT relative_path,file_size,modified_ns FROM {table}")}
            seen=set()
            for pos,path in enumerate(files,1):
                rel=path.relative_to(root).as_posix(); seen.add(rel); st=path.stat(); prior=existing.get(rel)
                if prior==(st.st_size,st.st_mtime_ns): summary.unchanged+=1
                else:
                    rec=self._read(path,rel,st.st_size,st.st_mtime_ns,library_kind)
                    summary.added += prior is None; summary.updated += prior is not None; summary.unreadable += not rec.readable
                    self._upsert(c,table,rec)
                if progress_callback: progress_callback(ArtworkIndexProgress(pos,len(files),rel))
            removed=sorted(set(existing)-seen)
            if removed:
                c.executemany(f"DELETE FROM {table} WHERE relative_path=?",((x,) for x in removed)); summary.removed=len(removed)
            c.execute("INSERT INTO app_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(f'{library_kind}_root',str(root)))
        summary.elapsed_seconds=round(time.perf_counter()-started,3); return summary

    def search(self,query:str='',extension:str|None=None,readable_only:bool=False,limit:int=500,offset:int=0,library_kind:str='original')->ArtworkSearchResult:
        table=self._table(library_kind); clauses=[]; params=[]
        if query.strip(): clauses.append('(filename LIKE ? COLLATE NOCASE OR relative_path LIKE ? COLLATE NOCASE)'); params += [f'%{query.strip()}%',f'%{query.strip()}%']
        if extension and extension!='All':
            ext=extension.lower(); ext=ext if ext.startswith('.') else '.'+ext; clauses.append('extension=?'); params.append(ext)
        if readable_only: clauses.append('readable=1')
        where='WHERE '+' AND '.join(clauses) if clauses else ''
        with self.database.connection() as c:
            total=c.execute(f'SELECT COUNT(*) FROM {table} {where}',params).fetchone()[0]
            rows=c.execute(f'SELECT * FROM {table} {where} ORDER BY filename COLLATE NOCASE LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
        records=[ArtworkRecord(r['id'],library_kind,r['relative_path'],r['filename'],r['stem'],r['extension'],r['width'],r['height'],r['file_size'],r['modified_ns'],r['sha256'],bool(r['readable']),r['indexed_at']) for r in rows]
        return ArtworkSearchResult(records,total)

    def extensions(self,library_kind='original')->list[str]:
        with self.database.connection() as c: return [r[0] for r in c.execute(f'SELECT DISTINCT extension FROM {self._table(library_kind)} ORDER BY extension')]

    def pipeline_counts(self)->dict[str,int]:
        with self.database.connection() as c:
            originals=c.execute('SELECT COUNT(*) FROM artwork_original').fetchone()[0]
            ready=c.execute("SELECT COUNT(*) FROM artwork_original o WHERE EXISTS(SELECT 1 FROM artwork_processed p WHERE p.stem=o.stem AND p.readable=1)").fetchone()[0]
            stale=c.execute("SELECT COUNT(*) FROM artwork_original o WHERE EXISTS(SELECT 1 FROM artwork_processed p WHERE p.stem=o.stem AND p.readable=1 AND p.modified_ns < o.modified_ns)").fetchone()[0]
        return {'TOTAL':originals,'READY':max(0,ready-stale),'NEEDS_REBUILD':stale,'NEEDS_PROCESSING':max(0,originals-ready)}

    def _table(self,kind):
        if kind not in {'original','processed'}: raise ValueError(kind)
        return 'artwork_original' if kind=='original' else 'artwork_processed'
    def _read(self,path,rel,size,mtime,kind):
        readable=True; width=height=0
        try:
            with Image.open(path) as im: im.verify()
            with Image.open(path) as im: width,height=im.size
        except Exception: readable=False
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        return ArtworkRecord(None,kind,rel,path.name,path.stem,path.suffix.lower(),width,height,size,mtime,sha,readable,self._now())
    def _upsert(self,c,table,r):
        c.execute(f'''INSERT INTO {table}(relative_path,filename,stem,extension,width,height,file_size,modified_ns,sha256,readable,indexed_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(relative_path) DO UPDATE SET filename=excluded.filename,stem=excluded.stem,extension=excluded.extension,width=excluded.width,height=excluded.height,file_size=excluded.file_size,modified_ns=excluded.modified_ns,sha256=excluded.sha256,readable=excluded.readable,indexed_at=excluded.indexed_at''',
        (r.relative_path,r.filename,r.stem,r.extension,r.width,r.height,r.file_size,r.modified_ns,r.sha256,int(r.readable),r.indexed_at))
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat()

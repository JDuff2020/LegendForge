from __future__ import annotations
from pathlib import Path
from app.models.project import Project
from app.models.results import ValidationItem, ValidationReport
from app.services.artwork_service import ArtworkService
from app.services.inventory_service import InventoryService
from app.services.pricing_service import PricingService

class ValidationService:
    def __init__(self):
        self.artwork=ArtworkService(); self.inventory=InventoryService(); self.pricing=PricingService()
    def validate(self,project:Project)->ValidationReport:
        items=[]; artwork_result=pricing_result=inventory_result=None
        for label,value,required in [('Original artwork',project.artwork_folder,True),('Processed artwork',project.processed_artwork_folder,True)]:
            path=Path(value)
            if path.is_dir():
                try:
                    result=self.artwork.scan(path)
                    if label=='Original artwork': artwork_result=result
                    status='PASS' if result.total_images and not result.unreadable_images else 'FAIL'
                    items.append(ValidationItem(label,status,f'{result.total_images:,} images; {result.unreadable_images} unreadable'))
                except Exception as exc: items.append(ValidationItem(label,'FAIL',str(exc)))
            else: items.append(ValidationItem(label,'FAIL' if required else 'OPTIONAL','Folder not found'))
        pp=Path(project.pricing_workbook)
        if pp.is_file():
            try:
                pricing_result=self.pricing.load(pp); items.append(ValidationItem('Pricing workbook','PASS',f'{len(pricing_result.deck_sizes)} deck sizes, {len(pricing_result.quantity_tiers)} tiers'))
            except Exception as exc: items.append(ValidationItem('Pricing workbook','FAIL',str(exc)))
        else: items.append(ValidationItem('Pricing workbook','FAIL','File not found'))
        ip=Path(project.inventory_workbook)
        if project.inventory_workbook and ip.is_file():
            try:
                inventory_result=self.inventory.load(ip); items.append(ValidationItem('Inventory workbook','PASS',f'{inventory_result.estimated_cards:,} estimated cards across {len(inventory_result.sheets)} sheets'))
            except Exception as exc: items.append(ValidationItem('Inventory workbook','FAIL',str(exc)))
        else: items.append(ValidationItem('Inventory workbook','OPTIONAL','Not selected yet'))
        op=Path(project.output_folder)
        try:
            op.mkdir(parents=True,exist_ok=True); probe=op/'.write_test'; probe.write_text('ok'); probe.unlink(); items.append(ValidationItem('Output folder','PASS','Writable'))
        except Exception as exc: items.append(ValidationItem('Output folder','FAIL',str(exc)))
        return ValidationReport(items,artwork_result,pricing_result,inventory_result)

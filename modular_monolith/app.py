import importlib.machinery
import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIRECTORIES = {
    "auth": "auth-service",
    "crm": "crm-service",
    "rh": "rh-service",
    "documents": "documents-service",
    "accounting": "accounting-service",
    "stock": "stock-service",
}


def _load_app(module_name: str, directory_name: str):
    source_directory = ROOT / directory_name
    package_name = f"{__package__}.modules.{module_name}"
    package_spec = importlib.machinery.ModuleSpec(package_name, loader=None, is_package=True)
    package_spec.submodule_search_locations = [str(source_directory)]
    package = importlib.util.module_from_spec(package_spec)
    sys.modules[package_name] = package

    app_spec = importlib.util.spec_from_file_location(f"{package_name}.app", source_directory / "app.py")
    if app_spec is None or app_spec.loader is None:
        raise ImportError(f"Não foi possível carregar o módulo {module_name}")
    module = importlib.util.module_from_spec(app_spec)
    sys.modules[app_spec.name] = module
    app_spec.loader.exec_module(module)
    return module.app


module_apps = {name: _load_app(name, directory) for name, directory in DOMAIN_DIRECTORIES.items()}
app = _load_app("web", "web-service")
app.state.module_apps = module_apps
stock_accounting = sys.modules[f"{__package__}.modules.stock.core.accounting_client"]
stock_accounting.set_accounting_app(module_apps["accounting"])
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def start_modules():
    for module_app in module_apps.values():
        await module_app.router.startup()


@app.on_event("shutdown")
async def stop_modules():
    for module_app in reversed(list(module_apps.values())):
        await module_app.router.shutdown()


@app.get("/health")
def health():
    return {"status": "ok", "modules": sorted(module_apps)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5002)
"""
Testes unitários da lógica de herança de permissões de pastas
(core.db.resolve_folder_permission), sem passar pela app nem por Postgres real.
Este é o mecanismo de segurança central do documents-service: decide quem pode
ver/editar/apagar cada documento, subindo a árvore de pastas até encontrar a
primeira política definida.
"""
import pytest

from core.db import resolve_folder_permission


class FakeCursor:
    """Fake mínimo que só entende as 3 queries usadas por resolve_folder_permission
    e get_user_profile."""

    def __init__(self, usuarios, folders, permissions):
        self.usuarios = usuarios
        self.folders = folders
        self.permissions = permissions
        self._rows = []

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = tuple(params or ())

        if s.startswith("SELECT u.id AS user_id"):
            uid, = p
            u = self.usuarios.get(uid)
            if not u:
                self._rows = []
                return
            self._rows = [{
                "user_id": uid, "nome": u.get("nome"), "email": u.get("email"),
                "is_admin": u.get("is_admin", False), "employee_id": u.get("employee_id"),
                "role": u.get("role"), "department_id": u.get("department_id"),
            }]
            return

        if s.startswith("SELECT * FROM document_permissions WHERE folder_id = %s"):
            fid, = p
            self._rows = list(self.permissions.get(fid, []))
            return

        if s.startswith("SELECT parent_id FROM folders WHERE id = %s"):
            fid, = p
            parent = self.folders.get(fid)
            self._rows = [{"parent_id": parent}] if fid in self.folders else []
            return

        raise AssertionError(f"SQL não esperado: {s!r}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _rule(scope_type, scope_value, can_view=False, can_edit=False, can_delete=False):
    return {"scope_type": scope_type, "scope_value": scope_value,
            "can_view": can_view, "can_edit": can_edit, "can_delete": can_delete}


def test_sem_folder_id_e_acesso_livre():
    cur = FakeCursor({}, {}, {})
    perm = resolve_folder_permission(cur, "user-1", None)
    assert perm == {"can_view": True, "can_edit": True, "can_delete": True}


def test_admin_tem_sempre_acesso_total_mesmo_com_regras_restritivas():
    usuarios = {"admin-1": {"is_admin": True}}
    permissions = {10: [_rule("company", None, can_view=False, can_edit=False, can_delete=False)]}
    cur = FakeCursor(usuarios, {10: None}, permissions)
    perm = resolve_folder_permission(cur, "admin-1", 10)
    assert perm == {"can_view": True, "can_edit": True, "can_delete": True}


def test_sem_regra_nenhuma_na_cadeia_e_acesso_livre_por_omissao():
    usuarios = {"user-1": {"is_admin": False}}
    folders = {10: None}
    cur = FakeCursor(usuarios, folders, {})
    perm = resolve_folder_permission(cur, "user-1", 10)
    assert perm == {"can_view": True, "can_edit": True, "can_delete": True}


def test_regra_de_departamento_aplica_se_utilizador_pertencer_ao_departamento():
    usuarios = {"user-1": {"is_admin": False, "department_id": 5}}
    permissions = {10: [_rule("department", "5", can_view=True, can_edit=False, can_delete=False)]}
    cur = FakeCursor(usuarios, {10: None}, permissions)
    perm = resolve_folder_permission(cur, "user-1", 10)
    assert perm == {"can_view": True, "can_edit": False, "can_delete": False}


def test_regra_de_departamento_nao_se_aplica_a_utilizador_de_outro_departamento():
    usuarios = {"user-1": {"is_admin": False, "department_id": 99}}
    permissions = {10: [_rule("department", "5", can_view=True, can_edit=True, can_delete=True)]}
    cur = FakeCursor(usuarios, {10: None}, permissions)
    perm = resolve_folder_permission(cur, "user-1", 10)
    assert perm == {"can_view": False, "can_edit": False, "can_delete": False}


def test_regra_company_aplica_se_a_todos_os_utilizadores():
    usuarios = {"user-1": {"is_admin": False}}
    permissions = {10: [_rule("company", None, can_view=True, can_edit=False, can_delete=False)]}
    cur = FakeCursor(usuarios, {10: None}, permissions)
    perm = resolve_folder_permission(cur, "user-1", 10)
    assert perm["can_view"] is True


def test_regra_de_utilizador_especifico():
    usuarios = {"user-1": {"is_admin": False}}
    permissions = {10: [_rule("user", "user-1", can_view=True, can_edit=True, can_delete=False)]}
    cur = FakeCursor(usuarios, {10: None}, permissions)
    perm = resolve_folder_permission(cur, "user-1", 10)
    assert perm == {"can_view": True, "can_edit": True, "can_delete": False}


def test_multiplas_regras_na_mesma_pasta_fazem_or_dos_direitos():
    usuarios = {"user-1": {"is_admin": False, "department_id": 5}}
    permissions = {10: [
        _rule("department", "5", can_view=True, can_edit=False, can_delete=False),
        _rule("user", "user-1", can_view=False, can_edit=True, can_delete=False),
    ]}
    cur = FakeCursor(usuarios, {10: None}, permissions)
    perm = resolve_folder_permission(cur, "user-1", 10)
    assert perm == {"can_view": True, "can_edit": True, "can_delete": False}


def test_heranca_sobe_ate_encontrar_a_primeira_pasta_com_regra():
    """Pasta 30 (sem regra) -> pasta 20 (sem regra) -> pasta 10 (com regra):
    a política de 10 deve aplicar-se a documentos dentro de 30."""
    usuarios = {"user-1": {"is_admin": False}}
    folders = {30: 20, 20: 10, 10: None}
    permissions = {10: [_rule("company", None, can_view=True, can_edit=False, can_delete=False)]}
    cur = FakeCursor(usuarios, folders, permissions)
    perm = resolve_folder_permission(cur, "user-1", 30)
    assert perm == {"can_view": True, "can_edit": False, "can_delete": False}


def test_regra_mais_proxima_tem_prioridade_sobre_regra_de_pasta_superior():
    usuarios = {"user-1": {"is_admin": False}}
    folders = {30: 10, 10: None}
    permissions = {
        10: [_rule("company", None, can_view=True, can_edit=True, can_delete=True)],
        30: [_rule("company", None, can_view=True, can_edit=False, can_delete=False)],
    }
    cur = FakeCursor(usuarios, folders, permissions)
    perm = resolve_folder_permission(cur, "user-1", 30)
    assert perm == {"can_view": True, "can_edit": False, "can_delete": False}

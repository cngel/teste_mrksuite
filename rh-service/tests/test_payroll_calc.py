"""Testes unitários puros de core/payroll_calc.py — sem app nem base de dados."""
from core.payroll_calc import calculate_gross_salary, calculate_deduction, calculate_payroll


def test_calculate_gross_salary_soma_todos_os_subsidios():
    profile = {"base_salary": 100000, "food_allowance": 5000, "transport_allowance": 3000, "other_allowance": 2000}
    assert calculate_gross_salary(profile) == 110000


def test_calculate_deduction_fixa():
    rule = {"calculation_type": "fixed", "value": 1500}
    assert calculate_deduction(rule, 100000) == 1500


def test_calculate_deduction_percentual():
    rule = {"calculation_type": "percentage", "value": 10}
    assert calculate_deduction(rule, 100000) == 10000


def test_calculate_deduction_tipo_desconhecido_devolve_zero():
    rule = {"calculation_type": "bracket", "value": 10}
    assert calculate_deduction(rule, 100000) == 0


def test_calculate_payroll_sem_deducoes():
    profile = {"base_salary": 100000, "food_allowance": 0, "transport_allowance": 0, "other_allowance": 0}
    result = calculate_payroll(profile, [])
    assert result["gross_salary"] == 100000
    assert result["total_earnings"] == 100000
    assert result["total_deductions"] == 0
    assert result["net_salary"] == 100000
    assert len(result["items"]) == 1
    assert result["items"][0]["description"] == "Salário base"


def test_calculate_payroll_inclui_subsidios_apenas_se_maiores_que_zero():
    profile = {"base_salary": 100000, "food_allowance": 5000, "transport_allowance": 0, "other_allowance": 0}
    result = calculate_payroll(profile, [])
    descriptions = [i["description"] for i in result["items"]]
    assert "Subsídio alimentação" in descriptions
    assert "Subsídio transporte" not in descriptions


def test_calculate_payroll_aplica_deducoes_percentuais_sobre_o_bruto():
    profile = {"base_salary": 100000, "food_allowance": 0, "transport_allowance": 0, "other_allowance": 0}
    rules = [{"name": "INSS", "calculation_type": "percentage", "value": 3}]
    result = calculate_payroll(profile, rules)
    assert result["total_deductions"] == 3000
    assert result["net_salary"] == 97000
    deduction_items = [i for i in result["items"] if i["item_type"] == "deduction"]
    assert deduction_items[0]["description"] == "INSS"
    assert deduction_items[0]["amount"] == 3000


def test_calculate_payroll_ignora_deducoes_de_valor_zero():
    profile = {"base_salary": 100000, "food_allowance": 0, "transport_allowance": 0, "other_allowance": 0}
    rules = [{"name": "Isenta", "calculation_type": "percentage", "value": 0}]
    result = calculate_payroll(profile, rules)
    assert result["total_deductions"] == 0
    assert all(i["item_type"] != "deduction" for i in result["items"])

from decimal import Decimal


def calculate_gross_salary(profile):

    return (
        profile["base_salary"]
        + profile["food_allowance"]
        + profile["transport_allowance"]
        + profile["other_allowance"]
    )


def calculate_deduction(rule, gross_salary):

    if rule["calculation_type"] == "fixed":

        return Decimal(rule["value"])


    elif rule["calculation_type"] == "percentage":

        return (
            gross_salary *
            Decimal(rule["value"]) /
            Decimal(100)
        )


    return Decimal(0)



def calculate_payroll(profile, deduction_rules):

    items = []


    # GANHOS

    base_salary = float(profile["base_salary"])
    food = float(profile["food_allowance"])
    transport = float(profile["transport_allowance"])
    other = float(profile["other_allowance"])


    items.append({
        "item_type": "earning",
        "description": "Salário base",
        "amount": base_salary
    })


    if food > 0:
        items.append({
            "item_type": "earning",
            "description": "Subsídio alimentação",
            "amount": food
        })


    if transport > 0:
        items.append({
            "item_type": "earning",
            "description": "Subsídio transporte",
            "amount": transport
        })


    if other > 0:
        items.append({
            "item_type": "earning",
            "description": "Outros subsídios",
            "amount": other
        })



    gross_salary = (
        base_salary +
        food +
        transport +
        other
    )



    total_deductions = 0



    # DEDUÇÕES

    for rule in deduction_rules:

        amount = 0


        if rule["calculation_type"] == "fixed":

            amount = float(rule["value"])


        elif rule["calculation_type"] == "percentage":

            amount = (
                gross_salary *
                float(rule["value"])
                / 100
            )


        if amount > 0:

            total_deductions += amount

            items.append({
                "item_type": "deduction",
                "description": rule["name"],
                "amount": amount
            })



    total_earnings = gross_salary


    net_salary = (
        total_earnings -
        total_deductions
    )


    return {

        "gross_salary": gross_salary,

        "total_earnings": total_earnings,

        "total_deductions": total_deductions,

        "net_salary": net_salary,

        "items": items
    }
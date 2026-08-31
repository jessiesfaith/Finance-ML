def present_value(future_value, discount_rate, years):
    """Calculate the present value of a future cash flow."""
    return future_value / ((1 + discount_rate) ** years)


if __name__ == "__main__":
    pv = present_value(
        future_value=1000,
        discount_rate=0.08,
        years=5
    )

    print(f"Present Value: ${pv:,.2f}")

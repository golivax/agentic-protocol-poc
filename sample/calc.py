def average(nums):
    total = 0
    for n in nums:
        total += n
    return total / len(nums)


def discount(price, pct):
    return price - price * pct / 100

"""Demo module for the reset-on-new-commit live test."""


def add(a, b):
    return a + b


def greet(name):
    # naming + no input validation — grist for the grumpy reviewer
    msg = "hello " + name
    return msg

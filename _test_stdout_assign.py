import sys

class X:
    pass

print("before:", type(sys.stdout).__name__)
sys.stdout = X()
print("after:", type(sys.stdout).__name__)
print("is X:", isinstance(sys.stdout, X))

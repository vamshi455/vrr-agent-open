import numpy as np
# Age (years)
age = np.array([2, 3, 4, 5, 6, 7, 8])
# Height (cm) - roughly matching typical child growth
height = np.array([86, 95, 102, 109, 115, 122, 128])

# Linear regression formula: y = mx + c
m, c = np.polyfit(age, height, 1)
print(f"Slope (m): {m:.2f}")
print(f"Intercept (c): {c:.2f}")
# Predict for age 9
print(f"Age 9 prediction: {m*9 + c:.2f}")

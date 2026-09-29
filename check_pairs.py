import json

path = "data/processed/lstm/train_pairs.jsonl"

count = 0
examples = []

with open(path, "r", encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)

        for field in ("prefix", "candidate"):
            value = row[field]
            controls = [
                (c, ord(c))
                for c in value
                if ord(c) < 32 and c not in "\t\r\n"
            ]

            if controls:
                count += 1
                if len(examples) < 20:
                    examples.append((field, repr(value), controls))

print("Rows containing control characters:", count)

for field, value, controls in examples:
    print(field, value, controls)
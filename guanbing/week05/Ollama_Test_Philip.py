from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama" # Ollama 默认无需真实 API Key，填任意值即可
)

response = client.chat.completions.create(
    model="qwen3:0.6b",
    messages=[
        {"role": "user", "content": "you are a smart and helpful assistant."},
        {"role": "user", "content": "Explain CFA, as a financial certification and set up a study plan for a beginner."}
    ],
    temperature=0.7,
    max_tokens=int(512*2)
)

print(response.choices[0].message.content or "(Model yields empty content)")
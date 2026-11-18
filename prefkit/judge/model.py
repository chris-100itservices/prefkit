"""The real judge: a language model loaded on the selected device.

Heavy imports happen inside the methods, so the rest of the project imports this file
on a machine without torch.
"""

from __future__ import annotations

from prefkit.compute.devices import DeviceInfo, select_device
from prefkit.compute.loader import load_judge
from prefkit.judge.prompting import build_prompt


class TransformersJudge:
    """Asks a chat model for one word, with greedy decoding.

    Greedy decoding means the model always picks its most likely next word. The same
    input then gives the same verdict, which makes results repeatable.
    """

    MAX_NEW_TOKENS = 4  # "TIE" is one short word; four tokens is ample

    def __init__(self, model_id: str, device: DeviceInfo | None = None) -> None:
        self.model_id = model_id
        self.device = device or select_device()
        self.model, self.tokenizer = load_judge(model_id, self.device)

    def __call__(self, question: str, answer_a: str, answer_b: str) -> str:
        import torch

        messages = [{"role": "user", "content": build_prompt(question, answer_a, answer_b)}]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(text, return_tensors="pt").to(self.device.torch_device)
        with torch.no_grad():
            output = self.model.generate(
                **inputs,
                max_new_tokens=self.MAX_NEW_TOKENS,
                do_sample=False,
            )
        new_tokens = output[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

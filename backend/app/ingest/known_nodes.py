CLIP_TEXT_ENCODE_CLASSES: list[str] = [
    "CLIPTextEncode",
    "CLIPTextEncodeSDXL",
    "CLIPTextEncodeSDXLRefiner",
    "BNK_CLIPTextEncodeAdvanced",
    "smZ CLIPTextEncode",
    "PromptWithStyle",
]

WILDCARD_ENCODE_CLASSES: list[str] = [
    "ImpactWildcardEncode",
    "ImpactWildcardProcessor",
    "WildcardProcessor",
]

CONCAT_NODE_CLASSES: list[str] = [
    "StringConcat",
    "ConcatString",
    "Text Concatenate",
    "String Concatenate",
    "CR Text Concatenate",
    "easy promptConcat",
]

SAMPLER_CLASSES: list[str] = [
    "KSampler",
    "KSamplerAdvanced",
    "SamplerCustom",
    "KSamplerSelect",
    "BNK_TiledKSampler",
    "KSamplerWithRefiner",
    "UltimateSDUpscale",
]

# Input field names that may hold text content (in priority order)
TEXT_INPUT_FIELDS: list[str] = [
    "populated_text",  # Actual value after wildcard expansion
    "text",
    "prompt",
    "positive",
    "wildcard_text",
]

# Nodes that store runtime text in widgets_values
TEXT_SHOW_NODE_CLASSES: list[str] = [
    "ShowText|pysssss",
    "ShowText",
    "Show Text",
    "DisplayText",
    "TextShow",
    "PreviewText",
    "Text Display",
]

# LLM-type nodes (their response may remain in widgets_values)
LLM_NODE_CLASSES: list[str] = [
    "Ollama",
    "OllamaVision",
    "OllamaGenerate",
    "OllamaGenerateV2",
    "OpenAI",
    "ChatGPT",
    "AnthropicClaude",
    "ClaudeAPI",
    "LLaVA",
    "IF_AI_tools",
    "Searge_LLM",
    "ComfyUI_Llama",
]

# Keywords used to identify negative prompts (content-based fallback)
NEGATIVE_KEYWORDS: list[str] = [
    "lowres",
    "bad anatomy",
    "bad hands",
    "worst quality",
    "low quality",
    "ugly",
    "blurry",
    "jpeg artifacts",
    "watermark",
    "signature",
    "error",
    "missing fingers",
    "extra digits",
    "deformed",
    "mutated",
    "disfigured",
    "bad proportions",
    "extra limbs",
    "cloned face",
    "gross proportions",
]

# Title keywords used to distinguish positive/negative prompts
POSITIVE_TITLE_KEYWORDS: list[str] = [
    "positive",
    "ポジティブ",  # Japanese: "positive"
    "prompt",
    "プロンプト",  # Japanese: "prompt"
]

NEGATIVE_TITLE_KEYWORDS: list[str] = [
    "negative",
    "ネガティブ",  # Japanese: "negative"
    "neg",
    "nega",
]

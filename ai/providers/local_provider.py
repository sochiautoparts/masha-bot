"""Local LLM Provider — Qwen3-4B via llama-cpp-python (GGUF).

Provides local inference for Masha Bot using llama-cpp-python with:
  - Qwen3-4B-Instruct Q4_K_M quantization (~2.5GB)
  - CPU-only inference (GitHub Actions compatible)
  - Thread-configurable for performance
  - Chat template formatting for instruction models
  - Automatic /no_think prefix for fast non-reasoning responses
  - Memory management with context window sizing
  - Fallback to cloud providers on failure

USAGE STRATEGY:
  Level 0 (LOCAL): Simple chat, comments, short responses
  Level 1-3 (CLOUD): Function routes, channel posts, VIN, diagnostics, vision

  Local model excels at:
    - Chat responses up to 2048 tokens (detailed user answers)
    - Group comments up to 512 tokens (short, fast, cheap)
    - Simple Q&A about BMW
    - Fallback when all cloud providers are down

  Cloud models are better for:
    - Channel post generation (needs creativity + quality)
    - VIN decoding (needs accuracy)
    - Diagnostics (needs expert knowledge)
    - Vision tasks (local model can't do vision)

TOKEN LIMITS (route-aware, enforced by ProviderManager):
  CHAT route:    max 2048 tokens (detailed user conversations)
  COMMENT route: max 512 tokens (short group/channel comments)
  FUNCTION route: max 2048 tokens (fallback for posts, VIN, diagnostics)
"""

import logging
import os
import time
import threading
from typing import Optional, List, Dict

from .base import BaseAIProvider, AIResponse

logger = logging.getLogger("masha.ai.local")

# ── Thread lock for llama-cpp-python — NOT thread-safe! ──
# Multiple concurrent calls to self._llm() cause segfaults.
_llama_lock = threading.Lock()

# ── Qwen3 chat template ──
# Qwen3 uses ChatML format with special tokens
QWEN3_SYSTEM_START = "<|im_start|>system\n"
QWEN3_USER_START = "<|im_start|>user\n"
QWEN3_ASSISTANT_START = "<|im_start|>assistant\n"
QWEN3_END = "<|im_end|>\n"


class LocalProvider(BaseAIProvider):
    """Local LLM provider using llama-cpp-python for GGUF models.

    Supports Qwen3-4B-Instruct with ChatML template formatting.
    CPU-only, designed for GitHub Actions runners (ubuntu-latest).
    """

    name = "local"

    def __init__(self):
        super().__init__(api_key=None)
        self._llm = None
        self._model_loaded = False
        self._available = False
        self._total_requests = 0
        self._total_errors = 0
        self._last_error_time = 0.0
        self._consecutive_errors = 0

    def _get_config(self):
        """Lazy import config to avoid circular imports."""
        from bot.config import config
        return config

    def _download_model(self) -> bool:
        """Download the GGUF model from HuggingFace if auto-download is enabled.

        Uses HF_TOKEN environment variable for authenticated downloads
        (required for gated models like Qwen3-4B-GGUF).
        Returns True if download succeeded or file already exists.
        """
        config = self._get_config()

        model_path = config.MODEL_PATH
        if not model_path:
            logger.warning("Local model: MODEL_PATH not set, cannot download")
            return False

        # Already exists
        if os.path.exists(model_path):
            size_mb = os.path.getsize(model_path) / (1024 * 1024)
            logger.info(f"Model file already exists: {model_path} ({size_mb:.1f} MB)")
            return True

        if not getattr(config, 'MODEL_AUTO_DOWNLOAD', False):
            logger.info("Auto-download disabled (MODEL_AUTO_DOWNLOAD=false)")
            return False

        download_url = getattr(config, 'MODEL_DOWNLOAD_URL', '')
        hf_token = os.getenv("HF_TOKEN", "")

        try:
            # Create models directory
            model_dir = os.path.dirname(model_path)
            if model_dir:
                os.makedirs(model_dir, exist_ok=True)

            # Method 1: Use huggingface_hub if available and HF_TOKEN is set
            if hf_token:
                try:
                    from huggingface_hub import hf_hub_download
                    logger.info("Downloading model via huggingface_hub (authenticated)...")

                    # Parse repo and filename from URL
                    if "huggingface.co/" in download_url:
                        parts = download_url.split("huggingface.co/")[1]
                        path_parts = parts.split("/resolve/")
                        if len(path_parts) >= 2:
                            repo_id = path_parts[0]
                            filename = path_parts[1].split("/", 1)[-1]

                            start_time = time.time()
                            downloaded_path = hf_hub_download(
                                repo_id=repo_id,
                                filename=filename,
                                token=hf_token,
                                local_dir=model_dir or ".",
                            )
                            elapsed = time.time() - start_time

                            # hf_hub_download may save to a different path — move if needed
                            if downloaded_path != model_path and os.path.exists(downloaded_path):
                                import shutil
                                shutil.move(downloaded_path, model_path)

                            if os.path.exists(model_path):
                                size_mb = os.path.getsize(model_path) / (1024 * 1024)
                                if size_mb > 100:
                                    logger.info(f"Model downloaded via HF hub: {size_mb:.1f} MB in {elapsed:.1f}s")
                                    return True

                    logger.warning("Could not parse HuggingFace URL, falling back to direct download")
                except ImportError:
                    logger.info("huggingface_hub not installed, falling back to direct download")
                except Exception as e:
                    logger.warning(f"HF hub download failed: {e}, falling back to direct download")

            # Method 2: Direct download via urllib with Bearer token
            if not download_url:
                logger.warning("MODEL_DOWNLOAD_URL not set, cannot download")
                return False

            import urllib.request

            logger.info(f"Downloading model from {download_url}")
            logger.info(f"Target: {model_path}")

            if hf_token:
                logger.info("Using HF_TOKEN for authenticated download")
                opener = urllib.request.build_opener()
                request = urllib.request.Request(download_url)
                request.add_header("Authorization", f"Bearer {hf_token}")
                response = opener.open(request)
                with open(model_path, 'wb') as f:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    block_size = 8192
                    last_report_pct = 0
                    while True:
                        chunk = response.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            pct = downloaded * 100 // total_size
                            if pct >= last_report_pct + 10:
                                logger.info(f"  Download: {pct}% ({downloaded // 1048576}/{total_size // 1048576} MB)")
                                last_report_pct = pct
            else:
                # No token — try unauthenticated download
                def report_progress(block_num, block_size, total_size):
                    downloaded = block_num * block_size
                    if total_size > 0:
                        percent = min(100, downloaded * 100 / total_size)
                        if block_num % 50 == 0:
                            logger.info(f"  Download: {percent:.0f}% ({downloaded // 1048576}/{total_size // 1048576} MB)")

                urllib.request.urlretrieve(download_url, model_path, reporthook=report_progress)

            # Verify download
            if not os.path.exists(model_path):
                logger.error("Download completed but file not found!")
                return False

            size_mb = os.path.getsize(model_path) / (1024 * 1024)
            if size_mb < 100:  # Sanity check — model should be ~2.5GB
                logger.error(f"Downloaded file too small ({size_mb:.1f} MB), likely corrupted. Removing.")
                os.remove(model_path)
                return False

            logger.info(f"Model downloaded: {size_mb:.1f} MB")
            return True

        except Exception as e:
            logger.error(f"Failed to download model: {e}")
            # Clean up partial download
            if os.path.exists(model_path):
                try:
                    os.remove(model_path)
                except Exception:
                    pass
            return False

    def _load_model(self) -> bool:
        """Load the GGUF model using llama-cpp-python.

        Automatically downloads model if file not found and MODEL_AUTO_DOWNLOAD=true.
        """
        if self._model_loaded and self._llm is not None:
            return True

        config = self._get_config()

        if not config.ENABLE_LOCAL_MODEL:
            logger.info("Local model DISABLED by config (ENABLE_LOCAL_MODEL=false)")
            return False

        model_path = config.MODEL_PATH
        if not model_path:
            logger.warning("Local model: MODEL_PATH not set")
            return False

        # Auto-download model if not found
        if not os.path.exists(model_path):
            logger.info(f"Model file not found at {model_path}, attempting auto-download...")
            if not self._download_model():
                logger.warning("Local model unavailable: file not found and download failed")
                return False

        try:
            from llama_cpp import Llama

            n_ctx = config.MODEL_N_CTX
            n_threads = config.MODEL_N_THREADS

            logger.info(
                f"Loading local model: {model_path} "
                f"(n_ctx={n_ctx}, n_threads={n_threads})"
            )

            start_time = time.time()

            self._llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_threads=n_threads,
                n_gpu_layers=0,  # CPU only — GitHub Actions has no GPU
                verbose=False,
                use_mlock=False,  # Don't lock memory — saves RAM
                use_mmap=True,    # Memory-mapped file — faster loading
                seed=42,          # Deterministic by default, temperature handles randomness
            )

            elapsed = time.time() - start_time
            self._model_loaded = True
            self._available = True

            # Warn if n_ctx is much smaller than model's training context
            try:
                n_ctx_train = getattr(self._llm, 'n_ctx_train', 0)
                if n_ctx_train > 0 and n_ctx < n_ctx_train:
                    logger.warning(
                        f"n_ctx ({n_ctx}) < n_ctx_train ({n_ctx_train}) — "
                        f"model capacity limited. If segfaults occur, increase MODEL_N_CTX."
                    )
            except Exception:
                pass

            logger.info(
                f"Local model loaded in {elapsed:.1f}s "
                f"(Qwen3-4B Q4_K_M, ctx={n_ctx}, threads={n_threads})"
            )
            return True

        except ImportError:
            logger.error(
                "llama-cpp-python not installed! "
                "Install with: CMAKE_ARGS='-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS' pip install llama-cpp-python"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to load local model: {e}")
            self._llm = None
            self._model_loaded = False
            self._available = False
            return False

    def _format_messages_chatml(self, messages: List[Dict[str, str]]) -> str:
        """Format messages using ChatML template (Qwen3 format).

        Applies /no_think prefix for fast non-reasoning responses.
        Limits conversation history to MODEL_HISTORY_LIMIT exchanges.
        """
        config = self._get_config()
        history_limit = config.MODEL_HISTORY_LIMIT

        # Limit history to reduce context length
        if len(messages) > history_limit + 1:  # +1 for system prompt
            system_msgs = [m for m in messages if m.get("role") == "system"]
            non_system = [m for m in messages if m.get("role") != "system"]
            limited_non_system = non_system[-history_limit:]
            messages = system_msgs + limited_non_system

        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                prompt += f"{QWEN3_SYSTEM_START}{content}{QWEN3_END}"
            elif role == "user":
                prompt += f"{QWEN3_USER_START}{content}{QWEN3_END}"
            elif role == "assistant":
                prompt += f"{QWEN3_ASSISTANT_START}{content}{QWEN3_END}"

        # Add assistant prefix for generation
        # /no_think tells Qwen3 to skip reasoning and answer directly
        prompt += f"{QWEN3_ASSISTANT_START}/no_think\n"

        return prompt

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 0,
        **kwargs,
    ) -> AIResponse:
        """Generate a chat completion using the local model.

        Uses ChatML formatting with /no_think for fast responses.
        Handles think tag cleanup automatically.
        """
        if not self._load_model():
            return AIResponse(
                text="",
                model="local-qwen3-4b",
                provider=self.name,
                error="Local model not available (not loaded or not enabled)",
            )

        # Circuit breaker: if too many consecutive errors, try reinitializing
        if self._consecutive_errors >= 3:
            elapsed_since_error = time.time() - self._last_error_time
            if elapsed_since_error < 120:  # 2-minute cooldown
                # Try to reinitialize model once
                if self._consecutive_errors == 3:
                    logger.warning("Local model: 3 consecutive errors — attempting reinitialize")
                    self.unload()
                    if self._load_model():
                        self._consecutive_errors = 0
                        logger.info("Local model reinitialized successfully")
                    else:
                        logger.error("Local model reinitialize failed — entering cooldown")
                return AIResponse(
                    text="",
                    model="local-qwen3-4b",
                    provider=self.name,
                    error=f"Local model in cooldown ({self._consecutive_errors} consecutive errors)",
                )
            else:
                self._consecutive_errors = 0  # Reset after cooldown

        config = self._get_config()
        max_tokens = max_tokens or config.MODEL_MAX_TOKENS

        try:
            # Format prompt using ChatML
            prompt = self._format_messages_chatml(messages)

            # Check prompt length vs context window
            estimated_tokens = len(prompt) // 3  # Conservative for Russian/CJK
            n_ctx = config.MODEL_N_CTX
            if estimated_tokens > n_ctx - max_tokens:
                logger.warning(
                    f"Prompt too long ({estimated_tokens} est. tokens vs {n_ctx} ctx), "
                    f"truncating history"
                )
                # Reduce history and try again
                truncated_messages = [messages[0]] + messages[-3:]  # System + last 3
                prompt = self._format_messages_chatml(truncated_messages)

            start_time = time.time()

            # Run inference in thread pool to avoid blocking event loop
            import asyncio
            loop = asyncio.get_event_loop()

            result = await loop.run_in_executor(
                None,
                self._generate,
                prompt,
                max_tokens,
                temperature,
            )

            elapsed = time.time() - start_time

            text = result

            if not text or len(text.strip()) < 3:
                self._consecutive_errors += 1
                self._last_error_time = time.time()
                return AIResponse(
                    text="",
                    model="local-qwen3-4b",
                    provider=self.name,
                    error="Empty or too short response from local model",
                )

            # Clean response
            text = self._clean_response(text)

            # Reset error tracking on success
            self._consecutive_errors = 0
            self._total_requests += 1

            logger.info(
                f"Local model response: {len(text)} chars, "
                f"{elapsed:.1f}s, tokens={max_tokens}"
            )

            return AIResponse(
                text=text,
                model="local-qwen3-4b",
                provider=self.name,
            )

        except Exception as e:
            self._consecutive_errors += 1
            self._last_error_time = time.time()
            self._total_errors += 1
            logger.error(f"Local model error: {e}")
            return AIResponse(
                text="",
                model="local-qwen3-4b",
                provider=self.name,
                error=str(e),
            )

    def _generate(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """Synchronous generation call (runs in thread pool).

        CRITICAL: llama-cpp-python is NOT thread-safe — concurrent calls
        cause segfaults (exit code 139). We use a global lock to ensure
        only one generation runs at a time.
        """
        acquired = _llama_lock.acquire(timeout=120)  # Wait up to 2 min
        if not acquired:
            logger.error("Local model: could not acquire lock (timeout 120s)")
            return ""

        try:
            # Reset KV cache before each call to prevent stale state
            try:
                self._llm.reset()
            except Exception:
                pass

            result = self._llm(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                top_k=40,
                repeat_penalty=1.1,
                stop=["<|im_end|>", "</s>", "<|im_start|>"],
            )
        except Exception as e:
            # Catch llama_decode errors before they cause segfaults
            logger.error(f"Local model generation error: {e}")
            # Mark model as potentially broken
            self._consecutive_errors += 1
            self._last_error_time = time.time()
            return ""
        finally:
            _llama_lock.release()

        # Extract text from result
        if isinstance(result, dict):
            choices = result.get("choices", [])
            if choices:
                text = choices[0].get("text", "")
                return text
        elif isinstance(result, str):
            return result

        return ""

    def _clean_response(self, text: str) -> str:
        """Clean local model response artifacts."""
        if not text:
            return ""

        import re

        # Remove think tags (Qwen3 reasoning)
        text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thinking\b[^>]*>.*?</thinking\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'</?think[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?thinking[^>]*>', '', text, flags=re.IGNORECASE)

        # Remove /no_think and /think prefixes
        text = re.sub(r'^/no_think\s*', '', text)
        text = re.sub(r'^/think\s*', '', text)

        # Remove ChatML artifacts
        text = text.replace("<|im_end|>", "")
        text = text.replace("</s>", "")
        text = text.replace("<|im_start|>", "")

        # Remove common AI prefixes (Masha-specific)
        for prefix in ["Маша:", "Masha:", "МАША:", "Assistant:", "Ответ Маши:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Strip quotes
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]

        # Strip markdown bold/italic
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    async def generate_image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        **kwargs,
    ) -> AIResponse:
        """Not supported — local model is text-only."""
        return AIResponse(
            error="Local model does not support image generation",
            provider=self.name,
            model="local-qwen3-4b",
        )

    def is_available(self) -> bool:
        """Check if local model is available for requests."""
        config = self._get_config()

        if not config.ENABLE_LOCAL_MODEL:
            return False

        if self._consecutive_errors >= 5:
            elapsed = time.time() - self._last_error_time
            if elapsed < 120:
                return False

        return self._model_loaded and self._llm is not None

    def get_status(self) -> dict:
        """Get status summary."""
        config = self._get_config()

        if not config.ENABLE_LOCAL_MODEL:
            return {"provider": self.name, "status": "DISABLED", "available": False}

        return {
            "provider": self.name,
            "status": "LOADED" if self._model_loaded else "NOT_LOADED",
            "available": self.is_available(),
            "model": "Qwen3-4B-Q4_K_M",
            "ctx": config.MODEL_N_CTX,
            "threads": config.MODEL_N_THREADS,
            "requests": self._total_requests,
            "errors": self._total_errors,
            "consecutive_errors": self._consecutive_errors,
        }

    def unload(self) -> None:
        """Unload model to free memory."""
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._model_loaded = False
            self._available = False
            logger.info("Local model unloaded (memory freed)")

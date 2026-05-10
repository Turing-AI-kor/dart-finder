import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    OPENDART_API_KEY: str = os.getenv("OPENDART_API_KEY", "")
    OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "./output"))
    CHECKPOINT_DIR: Path = Path(os.getenv("CHECKPOINT_DIR", "./checkpoint"))
    API_DELAY_SECONDS: float = float(os.getenv("API_DELAY_SECONDS", "0.1"))

    DART_BASE_URL: str = "https://opendart.fss.or.kr/api"

    INDUSTRY_KSIC_PREFIX = {
        "manufacturing":  ["10","11","12","13","14","15","16","17","18",
                           "19","20","21","22","23","24","25","26","27",
                           "28","29","30","31","32","33"],
        "service":        ["58","59","60","61","62","63"],
        "retail":         ["45","46","47"],
        "it_software":    ["58","62","63"],
        "finance":        ["64","65","66"],
        "construction":   ["41","42"],
    }

    def validate(self) -> None:
        if not self.OPENDART_API_KEY:
            raise RuntimeError(
                "OPENDART_API_KEY 가 없습니다.\n"
                "1) https://opendart.fss.or.kr/ 에서 인증키 신청\n"
                "2) .env 파일에 OPENDART_API_KEY=... 추가"
            )
        if len(self.OPENDART_API_KEY) != 40:
            raise RuntimeError(
                f"OPENDART_API_KEY 길이 오류 ({len(self.OPENDART_API_KEY)}자, 정상 40자)"
            )


settings = Settings()

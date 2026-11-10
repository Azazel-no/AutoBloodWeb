"""Compatibility wrapper for the live ScreenManager."""
from src.core.screen_manager import ScreenManager, ScreenError

GameNotFoundError=ScreenError


def capture_game():
    return ScreenManager().capture().image

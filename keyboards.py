def main_menu():
    return {
        "keyboard": [
            [
                {"text": "📊 مدیریت گروه‌ها"},
                {"text": "⚙️ تنظیمات"}
            ],
            [
                {"text": "🤖 دستیار هوشمند"},
                {"text": "📚 راهنما"}
            ]
        ],
        "resize_keyboard": True
    }


def remove_keyboard():
    return {
        "remove_keyboard": True
    }

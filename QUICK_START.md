# 🚀 Быстрый старт - Решение проблемы с Railway CLI

## ❌ Проблема
Команда `railway up` выдает ошибку "Your account is on a limited plan"

## ✅ Решение: Используйте GitHub

Railway CLI не работает на бесплатном плане. Используйте веб-интерфейс через GitHub:

---

## 📝 Пошаговая инструкция (5 минут)

### Шаг 1: Создайте репозиторий на GitHub

1. Откройте [github.com](https://github.com) и войдите
2. Нажмите **"New repository"**
3. Название: `telegram-reviews-bot` (или любое другое)
4. **ВАЖНО:** НЕ ставьте галочки на README, .gitignore, license
5. Нажмите **"Create repository"**

### Шаг 2: Загрузите код на GitHub

Выполните в PowerShell (в папке проекта):

```powershell
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ВАШ-USERNAME/telegram-reviews-bot.git
git push -u origin main
```

**Замените** `ВАШ-USERNAME` на ваш GitHub username!

### Шаг 3: Подключите Railway к GitHub

1. Откройте [railway.app](https://railway.app)
2. Нажмите **"New Project"**
3. Выберите **"Deploy from GitHub repo"**
4. Выберите ваш репозиторий `telegram-reviews-bot`
5. Railway автоматически начнет деплой

### Шаг 4: Добавьте переменные окружения

**В Railway:**

1. Откройте ваш проект
2. Перейдите в **"Settings"** → **"Variables"** (или просто **"Variables"**)
3. Добавьте две переменные:

   **Переменная 1:**
   - Name: `BOT_TOKEN`
   - Value: ваш токен от @BotFather
   - Пример: `8324522332:AAGy6qDs8j-uILme5ReWJXvmUdyUXHBONJY`

   **Переменная 2:**
   - Name: `ADMIN_IDS`
   - Value: ID администраторов через запятую
   - Пример: `8446467322,6555503209`

4. Нажмите **"Add"** для каждой переменной

### Шаг 5: Готово!

Railway автоматически перезапустит проект с новыми переменными.

Через 2-3 минуты ваш бот будет работать! 🎉

---

## 🔍 Как проверить что всё работает?

1. Откройте **"Deployments"** в Railway
2. Выберите последний деплой → **"View Logs"**
3. Вы должны увидеть:
   ```
   Starting bot...
   ```
4. Откройте вашего бота в Telegram
5. Отправьте `/start`
6. Бот должен ответить с меню!

---

## ⚠️ Если что-то не работает

### Бот не отвечает?
- Проверьте логи в Railway
- Убедитесь что переменная `BOT_TOKEN` правильная
- Проверьте что `BOT_TOKEN` добавлен в Variables

### Ошибка в логах?
- Скопируйте текст ошибки
- Проверьте что `requirements.txt` содержит нужные пакеты
- Убедитесь что Procfile правильный

### База данных не работает?
- SQLite создастся автоматически при первом запуске
- Ничего дополнительно делать не нужно

---

## 🎯 Полная инструкция

Смотрите файл `DEPLOY.md` для более подробной информации.


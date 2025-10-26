# 🚀 ДЕПЛОЙ СЕЙЧАС - ПРОСТЫЕ КОМАНДЫ

## ✅ Что уже сделано:
- ✅ Создан Procfile
- ✅ Создан runtime.txt  
- ✅ Изменения закоммичены в git

## 📋 ЧТО ДЕЛАТЬ СЕЙЧАС:

### 1. Создайте репозиторий на GitHub
1. Откройте: https://github.com/new
2. Repository name: `telegram-reviews-bot`
3. **НЕ** ставьте галочки на README, .gitignore, license
4. Нажмите **"Create repository"**

### 2. Загрузите код (выполните в PowerShell):

```powershell
cd C:\Users\makso\Desktop\ddddd

# Получите ссылку на ваш репозиторий (замените YOUR-USERNAME)
git remote add origin https://github.com/Thomas228-ship-it/telegram-reviews-bot.git

# Загрузите код
git push -u origin master
```

**Введите ваш GitHub username вместо YOUR-USERNAME!**

### 3. Подключите Railway
1. Откройте: https://railway.app
2. Нажмите **"New Project"** → **"Deploy from GitHub repo"**
3. Выберите `telegram-reviews-bot`
4. Дождитесь завершения деплоя (2-3 минуты)

### 4. Добавьте переменные окружения
В Railway:
- Settings → Variables → Add Variable
  - **BOT_TOKEN** = ваш токен от @BotFather
  - **ADMIN_IDS** = `8446467322,6555503209` (ваши ID)

---

## 🎉 ГОТОВО! Бот запущен!

---

## 📝 Полезные файлы:
- `QUICK_START.md` - подробная быстрая инструкция
- `DEPLOY.md` - полная инструкция по деплою
- `README.md` - документация бота


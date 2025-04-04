# PDF İnteraktif Asistan

Bu proje, PDF dosyalarınızla etkileşime geçip Gemini AI ile sorular sorabileceğiniz bir web uygulamasıdır.

## Vercel'e Yükleme Adımları

1. Vercel hesabınıza giriş yapın (veya kaydolun): https://vercel.com/

2. Projeyi GitHub, GitLab veya Bitbucket'a yükleyin. Önemli not: `.env` dosyasını yüklemediğinizden emin olun.

3. Vercel'de "New Project" (Yeni Proje) düğmesine tıklayın.

4. Yüklediğiniz GitHub/GitLab/Bitbucket deposunu seçin.

5. Aşağıdaki ortam değişkenlerini Vercel proje ayarlarından ekleyin:
   - `FLASK_SECRET_KEY`: Güvenli bir rastgele dize
   - `GOOGLE_API_KEY`: Google Gemini API anahtarınız
   - `SUPABASE_URL`: Supabase URL'niz
   - `SUPABASE_KEY`: Supabase genel API anahtarınız
   - `SUPABASE_SERVICE_KEY`: Supabase servis rolü API anahtarınız

6. "Deploy" (Dağıt) düğmesine tıklayın.

7. Dağıtım tamamlandığında, Vercel size bir URL verecektir (örneğin, `https://projeniz.vercel.app`).

## Önemli Notlar

- `.env` dosyasını GitHub veya herhangi bir halka açık depoya yüklemeyin.
- Vercel'e yüklendikten sonra, Supabase veritabanınızda "pdfs" ve "images" bucket'larını ve tablolarını manuel olarak oluşturmanız gerekebilir.
- Uygulama ilk çalıştırıldığında, Supabase veritabanı bağlantısını ve gereken bucket'ları otomatik olarak oluşturmaya çalışacaktır.

## Yerel Geliştirme

```bash
# Bağımlılıkları yükleyin
pip install -r requirements.txt

# Uygulamayı çalıştırın
python app.py
```

Tarayıcınızda `http://localhost:5000` adresine giderek uygulamayı görüntüleyebilirsiniz. 
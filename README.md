# TDA Archive

**TDA Archive**, Windows için hazırlanmış, tamamen yerel çalışan küçük bir arşiv yöneticisidir. Bu proje Türk Dünyası Asistanı (TDA) ürününe bağlı değildir; herhangi bir mevcut TDA kodunu, servisini veya hesabını kullanmaz.

Arayüz Python'un Tkinter bileşenlerini kullanır. Şifresiz `.tda` dosyaları standart ZIP arşividir. AES-256-GCM parola koruması seçildiğinde ZIP içeriği bu projeye ait şifreli kapsayıcıya sarılır. Uygulama arşiv içeriğini hiçbir servise göndermez ve çalışma sırasında internet bağlantısı kullanmaz.

## Hedef ve portable paket

Birincil hedef **Windows 10/11 x64**'tür. XP desteği bu sürümün kapsamında değildir. Son kullanıcı için amaç, Python ayrıca kurmadan açılan tek klasörlü bir taşınabilir pakettir: ZIP'i çıkarıp `TDAArchive.exe` çalıştırılır; uygulama çalışma zamanı ve bağımlılıklar aynı klasörde taşınır.

Bu teslimat Linux geliştirme ortamında hazırlandığından burada gerçek Windows `.exe` veya portable Windows ZIP'i üretilemedi. Aşağıdaki `build_windows.bat`, Windows 10/11 x64 üzerinde Python 3.12 x64 bulunan **build makinesinde** testleri çalıştırır, PyInstaller ile uygulama klasörünü oluşturur ve `release/TDAArchive-Win10-11-x64.zip` paketini hazırlar. Build makinesinde ilk bağımlılık kurulumunda internet gerekir; portable paketi çalıştıran son kullanıcıda Python veya internet gerekmez. Bu Linux ortamında Windows derlemesi veya native Windows çalışma testi yapılmış değildir.

Windows'ta derlemek için:

1. Build makinesine Python 3.12 x64 kurun (Tkinter dahil).
2. Proje klasöründe `build_windows.bat` çalıştırın.
3. Dağıtım dosyası `release/TDAArchive-Win10-11-x64.zip` olur. ZIP'in tüm içeriği birlikte dağıtılmalı; yalnızca `.exe` kopyalanmamalıdır.

Portable çıktı imzalanmaz; Windows SmartScreen uyarı gösterebilir. Paket yalnızca Windows 10/11 x64 içindir; Windows XP desteklenmez.

## Kaynaktan çalıştırma (geliştirme)

Windows 10/11'de Python 3.10 veya üzerini kurun; kurulumda **Add Python to PATH** seçeneğini ve Tkinter bileşenini seçin. Ardından:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

İsteğe bağlı `install_windows.bat` sanal ortam ve bağımlılıkları kurar; `start_windows.bat` kaynak koddan uygulamayı başlatır. Sürükle-bırak `tkinterdnd2` kurulu olduğunda etkinleşir.

## Uygulama özellikleri

- `.tda` arşivi oluşturma ve mevcut arşivi açma.
- Bir veya birden çok dosya, ayrıca klasör ve alt klasörleri ekleme.
- Seçili dosya/klasörü yeniden adlandırma veya arşivden silme.
- Arşiv içeriğini klasör ağacı halinde gösterme; dosya sayısı, içerik boyutu, arşivin diskte kapladığı boyut ve tür bilgisi.
- Dosya veya klasörü ya da tüm arşivi bilgisayara çıkarma. Var olan dosyaların üzerine yazılmaz.
- UTF-8 metin, CSV, JSON, kod ve benzeri metin türlerini; ayrıca PNG/GIF görsellerini önizleme.
- AES-256-GCM ile isteğe bağlı parola koruması ve arşiv doğrulama.
- Açık/koyu tema ve paket kurulu olduğunda sürükle-bırak.

PDF, DOCX, XLSX ve diğer dosya türleri arşive eklenebilir, saklanabilir ve çıkarılabilir. Bu sürüm bunları uygulama içinde önizlemez. Metin önizlemesi 8 MiB, görsel önizlemesi 16 MiB ile sınırlıdır.

## TDA Archive 1.0 dosya biçimi

### Şifresiz arşiv

`.tda`, standart ZIP dosyasıdır. ZIP kökünde UTF-8 kodlu `manifest.json`, içerik dosyaları `content/<göreli-yol>` altında bulunur. Manifestte biçim adı (`TDA Archive`), sürüm (`1.0`), oluşturulma zamanı ve her kayıt için yol/tür; dosyalar için bayt boyutu ve SHA-256 özeti yer alır. Klasörler açık kayıt olarak tutulur; böylece boş klasörler de korunur. ZIP CRC ve manifestteki SHA-256 açılışta ve doğrulama komutunda kontrol edilir.

Şifresiz dosyanın içi normal ZIP yazılımıyla okunabilir. Arşiv biçiminin başlığı ve manifesti başka yazılımların temel yapıyı uygulamasına yetecek şekilde sabittir.

### Parola korumalı arşiv

Şifreli dosya, `TDAENC1\n` (8 bayt) imzası, rastgele 16 bayt salt, rastgele 12 bayt nonce, şifreli ZIP verisi ve sondaki 16 bayt GCM etiketi sırasıyla içerir. Anahtar, UTF-8 parola ile scrypt (`N=32768, r=8, p=1`) kullanılarak türetilir; şifreleme AES-256-GCM'dir. Sabit başlık GCM ek doğrulama verisi (AAD) olarak doğrulanır. Parola arşive yazılmaz. Parola unutulursa kurtarma veya arka kapı yoktur.

Şifreleme için `cryptography` paketi gerekir. Şifreli içerik çözülürken uygulama ZIP verisini yalnızca işlem belleğinde tutar; diske geçici bir şifresiz arşiv yazmaz.

## Güvenlik ve sınırlar

- Arşiv yolları göreli ve kanonik olmalıdır. `..`, mutlak/Windows sürücü yolları, ters eğik çizgi, Windows'ta geçersiz/ayrılmış adlar reddedilir. Büyük/küçük harf veya Unicode normalizasyonu farkıyla çakışan yollar da kabul edilmez.
- Sembolik bağlantı ve özel dosya türleri kabul edilmez. Çıkarma `ZipFile.extract()` kullanmaz; hedefin dışına çıkma denetimi yapılır ve var olan dosya üzerine yazılmaz.
- Bozuk ZIP, hatalı manifest, yinelenen yollar, boyut uyuşmazlığı, SHA-256/CRC hatası ve yanlış parola kontrollü hata ile reddedilir.
- Şifreli `.tda` dosyasının üst sınırı 256 MiB'dir (şifreleme kapsayıcısı dahil). Bir arşivde açılmış verinin toplam sınırı 2 GiB, kayıt sayısı 100.000'dir.
- Parola ayarlara veya arşive kaydedilmez; arşiv açıkken süreç belleğinde tutulur ve arşiv kapanınca nesnedeki parola referansı bırakılır. İşletim sistemi belleğinin güvenli biçimde sıfırlandığı garanti edilmez.
- Portable uygulamanın kendisi ağ bağlantısı açmaz. Build makinesinde paketleri indirmek için internet gerekebilir.

## Testler

Proje klasöründe:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Test paketi ZIP uyumluluğu, oluşturma/açma/ekleme/yeniden adlandırma/silme/çıkarma, üzerine yazmama, yol saldırısı reddi, bozuk içerik/SHA doğrulaması, yanlış parola ve AES-GCM tahrifat denetimini kapsar.

Bu proje Linux geliştirme ortamında **12 otomatik testle** ve sanal ekran üzerinde Tkinter arayüzünü açıp örnek arşivi yükleme/tema değiştirme smoke testiyle doğrulandı. Bunlar Windows `.exe` derlemesi veya native Windows 10/11 çalışma testi değildir; bunlar `build_windows.bat` ile Windows makinede ayrıca yapılmalıdır.

## Proje dosyaları

- `app.py` — Türkçe masaüstü arayüzü.
- `archive_format.py` — TDA Archive 1.0 biçimi ve dosya işlemleri.
- `tests/` — otomatik testler.
- `samples/ornek.tda` — örnek şifresiz `.tda` arşivi.
- `PORTABLE_README.txt` — portable paket son kullanıcı notu.
- `install_windows.bat`, `start_windows.bat`, `build_windows.bat` — kaynak çalışma ve Windows portable derleme yardımcıları.

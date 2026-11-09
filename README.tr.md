# reddit-wiper

**Kendi Reddit hesabınızdaki tüm yorum ve gönderileri silin. Açık kaynak, tamamen yerel, varsayılan olarak güvenli.**

[English README](README.md)

- **Varsayılan mod deneme (dry run).** `--execute` eklemeden ve kullanıcı adınızı yazmadan hiçbir şey değişmez.
- **Önce üzerine yaz, sonra sil.** Yorumlar ve metin gönderileri önce rastgele metinle düzenlenir, sonra silinir.
- **1000 öğe sınırını aşar.** Tüm listeleri tarar (new, hot, tüm zaman aralıklarında top ve
  controversial), sildikten sonra yeniden tarar ve **Reddit veri dışa aktarımınızı** okuyarak
  şimdiye kadar paylaştığınız her şeye ulaşabilir.
- **Önce yedek alır.** Silinecek her şey önce yerel bir JSON ve CSV dosyasına kaydedilir.
- **Kaldığı yerden devam eder.** Yarıda kesilirse aynı komutu tekrar çalıştırmanız yeterli.
- **İki mod.** *API modu* kendi Reddit "script" uygulamanızı kullanır. *Tarayıcı modu* API
  uygulaması gerektirmez; açılan tarayıcı penceresinde kendiniz giriş yaparsınız.
- **Telemetri yok.** Araç yalnızca reddit.com ile konuşur. Başka hiçbir yere veri gönderilmez.
- Windows, macOS ve Linux'ta, Python 3.10 ve üzeriyle çalışır.

> [!WARNING]
> **Silme kalıcıdır.** Reddit'te geri alma yoktur. `--execute` kullanmadan önce [Uyarılar](#uyarılar) bölümünü okuyun.

---

## Kurulum

Python 3.10 veya üzeri gerekir. En kolayı [pipx](https://pipx.pypa.io/) ile kurmaktır:

```bash
pipx install reddit-wiper
```

Tarayıcı modu için opsiyonel eki kurun ve bir kereliğine tarayıcı indirin:

```bash
pipx install "reddit-wiper[browser]"
reddit-wiper install-browser
```

`install-browser`, Playwright'ın Chromium'unu indirir (yaklaşık 150 MB). Bilgisayarınızda Google
Chrome veya Microsoft Edge varsa bu adımı atlayıp `--channel chrome` ya da `--channel msedge`
kullanabilirsiniz.

## Hangi modu seçmeli?

| | API modu (`reddit-wiper wipe`) | Tarayıcı modu (`reddit-wiper browser`) |
|---|---|---|
| Gereken | **Reddit tarafından onaylanmış** kendi "script" uygulamanız | Hiçbir şey; tarayıcıda giriş yaparsınız |
| Şifreniz | Araca verilir (yalnızca Reddit'e gönderilir) | Araca hiç verilmez |
| 2FA | Desteklenir (`--otp`) | Desteklenir (normal giriş yaparsınız) |

**Reddit artık API erişimi için onay istiyor.** Reddit'in
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
sayfasına göre API üzerinden veriye erişmeden önce erişim talep etmeniz ve açık onay almanız
gerekiyor. Yeni oluşturulan bir uygulama, Reddit onaylayana kadar çalışmayabilir. Onay zaman
alabilir ve reddedilebilir. **Onaylı bir uygulama alamıyorsanız tarayıcı modunu kullanın.**

## API modu: Reddit uygulamanızı oluşturun

Her kullanıcı **kendi** uygulamasını oluşturur. Bu repoda hiçbir client ID veya secret yoktur.

1. Reddit'e giriş yapın ve <https://www.reddit.com/prefs/apps> adresini açın.
2. **"are you a developer? create an app..."** (ya da **"create another app..."**) düğmesine tıklayın.
3. Formu doldurun:
   - **name**: istediğiniz bir ad, ör. `my-wiper`
   - **type**: **script** seçin
   - **redirect uri**: `http://localhost:8080` (form istiyor, kullanılmıyor)
4. **create app** düğmesine tıklayın. İstenirse geliştirici koşullarını okuyup kabul edin.
5. İki değeri not edin:
   - **client ID**: "personal use script" yazısının hemen altındaki kısa metin
   - **secret**: "secret" yazısının yanındaki değer
6. **API erişimi talep edin.** O sayfadaki ya da
   [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
   sayfasındaki erişim talebi bağlantısını izleyin. Kullanım amacınızı dürüstçe yazın: *"kendi
   hesabımdaki kendi yorum ve gönderilerimi silen kişisel bir script; ticari değil; veri
   saklanmıyor veya paylaşılmıyor"*. Onayı bekleyin. Reddit bu süreci zaman zaman değiştiriyor;
   güncel bilgi için Reddit'in kendi sayfaları esastır.
7. Kurulum sihirbazını çalıştırın:

   ```bash
   reddit-wiper setup
   ```

   Sihirbaz client ID, secret, kullanıcı adı, 2FA kullanıp kullanmadığınızı ve şifrenin kaydedilip
   kaydedilmeyeceğini sorar. Varsayılan cevap **hayır**dır; bu durumda şifre her çalıştırmada sorulur.

**İki adımlı doğrulama (2FA):** Hesabınızda 2FA açıksa kurulumda "evet" deyin ya da `--otp`
kullanın. 6 haneli kod sorulur ve PRAW'ın beklediği `şifre:123456` biçiminde gönderilir. Reddit'in
giriş anahtarı yaklaşık bir saat geçerlidir ve yeni kod olmadan yenilenemez. Bu yüzden 2FA ile çok
uzun süren bir çalışma giriş hatasıyla durabilir. Aynı komutu yeni bir kodla tekrar çalıştırın;
kaldığı yerden devam eder.

## Tarayıcı modu

```bash
reddit-wiper browser             # deneme
reddit-wiper browser --execute   # gerçekten sil
```

1. old.reddit.com giriş sayfasında bir tarayıcı penceresi açılır.
2. **Giriş işlemini 2FA dahil kendiniz yaparsınız.** Şifreniz yalnızca Reddit'e gider, araç görmez.
3. Terminale dönüp **Enter**'a basarsınız.
4. Araç, giriş yapılmış oturumunuz üzerinden öğelerinizi listeler ve önizleme gösterir.
   `--execute` ile, kullanıcı adınızı yazdıktan sonra, old.reddit.com'daki "edit" ve "delete"
   düğmelerinin gönderdiği isteklerin aynısıyla öğeleri düzenleyip siler. Varsayılan olarak
   öğeler arasında 2 saniye bekler.

**Yalnızca bir kez giriş yaparsınız.** Tarayıcı profili (Reddit oturumunuzla birlikte) aracın
gizli veri klasöründe saklanır, böylece sonraki çalıştırmalar oturum açık olarak başlar. Bu
önemlidir: Her seferinde yepyeni bir tarayıcıdan tekrar tekrar giriş yapmak Reddit'e her
seferinde yeni bir cihaz gibi görünür ve **Reddit hesabı kilitleyip şifre değişikliği
isteyebilir**. Kayıtlı profil Reddit oturum çerezlerinizi içerdiği için o klasörü bir şifre gibi
koruyun. `reddit-wiper browser-logout` ile istediğiniz zaman silebilir ya da `--fresh-profile` ile
tarayıcı kapanınca silinen geçici bir profil kullanabilirsiniz.

Deneme modu, onay, yedek, filtreler, veri dışa aktarımı ve devam etme özelliklerinin hepsi API
modundakiyle aynıdır.

## Örnek komutlar

```bash
# Neyin silineceğini gör (hiçbir şey değişmez)
reddit-wiper wipe

# İlk gerçek deneme: yalnızca en eski tek öğeyi sil (yeniden tarama yapılmaz)
reddit-wiper wipe --limit 1 --execute

# Her şeyi gerçekten sil (önce kullanıcı adınızı yazmanız istenir)
reddit-wiper wipe --execute

# Sadece yorumlar, sadece iki subreddit'te
reddit-wiper wipe --only comments -s AskReddit -s pics --execute

# Bir subreddit hariç her şey
reddit-wiper wipe -x python --execute

# 2023'ten eski öğeler; puanı 100'den yüksek olanlar kalsın
reddit-wiper wipe --before 2023-01-01 --max-score 100 --execute

# Veri dışa aktarımındaki her şeyi dahil et (1000 sınırını aşar)
reddit-wiper wipe --export ~/Downloads/export_kullaniciadi_20260101.zip --execute

# Üzerine yazmadan ve yedek almadan sil
reddit-wiper wipe --no-overwrite --no-backup --execute

# Aynı seçenekler tarayıcı modunda da var
reddit-wiper browser --only posts --after 2020-01-01 --execute

# İlerleme durumu, dosya konumları ve ilerlemeyi sıfırlama
reddit-wiper status
reddit-wiper reset kullaniciadi
```

Tüm seçenekler için `reddit-wiper wipe --help` komutunu çalıştırın.

## 1000 öğe sınırını aşmak: veri dışa aktarımı

Reddit listeleri her tür için yalnızca en yeni ~1000 öğeyi döndürür. Araç birçok listeyi tarar ve
her silme turundan sonra yeniden tarar; bu genelde daha fazlasını ortaya çıkarır. **Her şeyi**
sildiğinizden emin olmak için veri dışa aktarımınızı kullanın:

1. <https://www.reddit.com/settings/data-request> adresini açın (Ayarlar, Gizlilik, "Request your data").
2. GDPR (veya CCPA) talep seçeneğini seçip gönderin.
3. Veriler hazır olduğunda Reddit size mesaj gönderir. Bu birkaç gün, bazen birkaç hafta sürebilir.
4. ZIP dosyasını indirin. Açmanıza gerek yok.
5. `--export dosya/yolu.zip` (ya da açılmış klasör) ile çalıştırın.

Araç yalnızca `comments.csv` ve `posts.csv` dosyalarını okur. Her öğenin güncel durumunu Reddit'ten
kontrol eder ve zaten silinmiş olanları atlar. Dışa aktarımdaki IP adresleri okunmaz.

## Dosyalar nerede saklanır?

Proje klasörüne hiçbir şey yazılmaz. Tam yolları görmek için `reddit-wiper status` komutunu çalıştırın.

| Ne | Windows | macOS | Linux |
|---|---|---|---|
| Kimlik bilgileri | `%LOCALAPPDATA%\reddit-wiper\credentials.env` | `~/Library/Application Support/reddit-wiper/` | `~/.config/reddit-wiper/` |
| Yedek, ilerleme, kayıtlı tarama, log | `%LOCALAPPDATA%\reddit-wiper\{backups,state,scans,logs}` | `~/Library/Application Support/reddit-wiper/` | `~/.local/share/reddit-wiper/` |

**Yedekler ve kayıtlı taramalar öğelerinizin metnini içerir.** Bunları gizli tutun ya da işiniz
bitince silin (`reddit-wiper reset KULLANICIADI` kayıtlı taramayı ve ilerlemeyi siler).

## Gizlilik ve güvenlik

- **Telemetri, analitik veya güncelleme kontrolü yoktur.** Araç yalnızca `reddit.com` ile konuşur.
- Client secret, şifre, 2FA kodları, token'lar ve oturum anahtarları **asla** konsola veya log
  dosyalarına yazılmaz. Ek güvenlik önlemi olarak loglar bunlara karşı ayrıca filtrelenir.
- Şifre, `setup` sırasında açıkça seçmediğiniz sürece kaydedilmez.
- Güvenlik açığı bildirmek için [SECURITY.md](SECURITY.md) dosyasına bakın.

## Uyarılar

- **Silme işlemi geri alınamaz.** Önce deneme modunda çalıştırın ve yedeği saklayın.
- **Kopyalar başka yerlerde olabilir.** Üçüncü taraf arşivler (ör. Arctic Shift, PullPush, eski
  Pushshift dökümleri), arama motoru önbellekleri ve Wayback Machine eski içeriklerinizi
  kopyalamış olabilir. Reddit'ten silmek bu kopyaları silmez; her biri için ayrıca kaldırma
  talebinde bulunmanız gerekir.
- **Reddit kuralları geçerlidir.** Araç, Reddit'in hız sınırlarına uyar (PRAW'ın kendi mekanizması
  ve HTTP 429'da üstel bekleme) ve yalnızca kendi hesabınız için tasarlanmıştır.
- Bir gönderiyi silmek, altındaki başkalarının yorumlarını silmez.
- Mesajlar, sohbetler, oylar, kaydedilenler ve hesabın kendisi silinmez.

## SSS

**Deneme modu gerçekten zararsız mı?** Evet. `--execute` olmadan araç Reddit'ten yalnızca okuma yapar.
Yazdığı tek şey yerel bir log dosyası ve kayıtlı taramadır.

**Sahip olduğumdan daha az öğe buldu.** Bunun sebebi 1000 öğe sınırı. `--execute` kullanın (her
turdan sonra yeniden tarama yapılır) ve/veya `--export` ile veri dışa aktarımınızı verin.

**Ctrl+C'ye basarsam ya da bilgisayar uykuya geçerse ne olur?** İlerleme her öğeden sonra kaydedilir.
Aynı komutu tekrar çalıştırın; biten öğeler atlanır.

**Bazı öğeler hata verdi.** Sebebiyle birlikte loglanırlar ve komutu bir sonraki çalıştırışınızda
tekrar denenirler. Kilitli ve arşivlenmiş öğeler genelde düzenlenemez ama silinebilir.

**API modunda "Login failed" hatası alıyorum.** Client ID, secret, kullanıcı adı ve şifreyi kontrol
edin. Uygulama türünün **script** olduğundan ve Reddit'in API erişiminizi onayladığından emin olun.
2FA kullanıyorsanız `--otp` ekleyin.

**Tarayıcı modu sürekli "rate limited" diyor.** Reddit, web sayfalarının ne kadar hızlı
yüklenebileceğini API'ye göre çok daha sıkı sınırlar. Araç sayfalar arasında 2 saniye bekler ve
Reddit "yavaşla" dediğinde giderek daha uzun bekler. Sınır kalkmazsa tarama erken biter ve araç o
ana kadar bulduğu öğelerle devam eder. Her tarama 24 saat boyunca kaydedilir; böylece deneme
modundan sonraki `--execute` çalıştırması Reddit'i baştan taramaz. Yeni bir tarama için
`--rescan` ekleyin (ör. yarıda kalan bir taramanın kaçırdıklarını sonra bulmak için). Aynı anda
birden fazla kopya çalıştırmayın.

**Hesabımı da siler mi?** Hayır, yalnızca içeriği siler. Hesabı silmek isterseniz Reddit ayarlarından silin.

## Lisans

[MIT](LICENSE). Bu proje Reddit, Inc. ile bağlantılı değildir ve Reddit tarafından onaylanmamıştır.

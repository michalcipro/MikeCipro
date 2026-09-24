# michalcipro.com

Osobní web Michala Cipra: mentální koučink, mentoring, tenis a vibecoding.

Statický web bez build kroku: `index.html`, `assets/css/style.css`, `assets/js/main.js`.

## Úprava obsahu

- Všechny texty jsou přímo v `index.html`.
- Sekce, které zatím obsahují ukázkový text, mají štítek `<span class="sample">…</span>`. Po doplnění skutečných údajů štítek smaž.
- Portrét: do bloku `.portrait` vlož `<img src="assets/img/michal.jpg" alt="Michal Cipro">`.
- E-mail je v sekci Kontakt dvakrát (odkaz `mailto:` a text pro kopírování).

## Lokální náhled

```sh
npx serve .
```

## Nasazení na michalcipro.com (GitHub Pages)

1. V repozitáři: Settings → Pages → Deploy from a branch → `main`, složka `/ (root)`.
2. Soubor `CNAME` už obsahuje doménu `michalcipro.com`.
3. U registrátora domény nastav A záznamy na `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153` a záznam CNAME pro `www` na `michalcipro.github.io`.
4. Po ověření domény zapni v nastavení Pages volbu Enforce HTTPS.

Web funguje i na Netlify, Vercelu nebo Cloudflare Pages, stačí nahrát složku repozitáře.

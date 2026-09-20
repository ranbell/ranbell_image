# Muse — Creator's Guide

**Ranbell Image v0.4.0**

日本語版はこちら → [muse.ja.md](muse.ja.md)

Muse is a studio where you are the Showrunner, and a Muse (an actress on the lot) sits for the shot.

- You talk with her and slowly lock composition, props, and direction.
- She puts one test on the board — "look good?" — and a yes becomes the final.

**Talking with Muse *is* the shoot.**

The internals live in the [technical reference](../tech/muse.md).

**Setup.** Get Ranbell Image running first: [INSTALLATION.md](../../INSTALLATION.md). This page is how to shoot once the studio is up.

![Muse roster](../screenshots/muse/JA_muse_00_muse.png)

![The studio](../screenshots/muse/JA_muse_01_overview.png)

---

## 1. What can you do?

The shoot goes like this.

1. **Talk** — "Let's shoot in the library at dusk. Sit by the window, book open."
2. **It lands in the ledger** — place, clothes, pose, face, field by field
3. **Test shot** — one full-size still. "Look good?"
4. **Final** — if you like it, the same picture, finished

What actually gets drawn is whatever is in the ledger from step 2.
If that is not what you meant, say it again and the ledger updates.

---

## 2. Open the panel and start

> **Important — these are the two models the studio was built on.**
>
> Muse will "kind of" run on any LLM and any image model. A shoot you will actually like is this pair.
>
>
> |               | Use this                         | If you skip it                                                                                          |
> | ------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------- |
> | **LLM**       | **Gemma 4 26B** (A4B)            | Chat and ledger will not land. A small model makes both the gate and the shoot disappointing.           |
> | **Image model** | An **Anima / Krea2-family** workflow | It will not read the prompt Muse writes. A model that cannot follow natural language will not get you there. |
>
>
> After a test shot she looks at the board and talks about the picture. Gemma 4 26B can read images, so that turn can stay on the same model. Another LLM often fails silently — no error, just an empty reply.

Before you start, gather the following. **Open** stays locked until they are set.
Whatever is missing is listed under the button.

1. **Muse (the actress)** — tap her name card. A partner is the same shape (clear her with the ✕ next to the card)
2. **Kind of shoot** — Just her / with a partner / Studio. Locked once you open
3. **Crew** — Studio only. Pick one of the crew cards
4. **LLM** and **image model (workflow)** — pick **Gemma 4 26B** and an **Anima-family** workflow, as above

![Settings before you open](../screenshots/muse/JA_muse_02_start.png)



### LLM and image model sit in two places


|                     | Where                                                        | When it applies                    |
| ------------------- | ------------------------------------------------------------ | ---------------------------------- |
| **This shoot only** | ⚙ settings in the Muse panel                                 | For the session you have open      |
| **Standing default** | Main screen admin → “Muse default LLM / default image model” | Already filled the next time you open |


Set the standing defaults and Muse opens already filled, so you can press Open.
Leave them empty and pick for that day's work.

Enter in the chat box is a **newline**. Send is the send button, so confirming kanji will not fire the line.

![Muse settings and admin defaults](../screenshots/muse/JA_muse_10_settings.png)



---



## 3. Talk, and the ledger moves

During a shoot, what you asked for — place, clothes — and what she chose — pose, and so on — all live in the **ledger**.
A field is not a pile. The whole field becomes the new direction.


| Field             | What it holds                    | Example                              |
| ----------------- | -------------------------------- | ------------------------------------ |
| 📍 `scene`        | Place and time                   | a school library, evening            |
| 🏞 `bg`           | What is in frame that is not her | bookshelves, a window seat           |
| 💡 `light`        | How the light falls              | backlighting, rim_light              |
| 🌫 `atmosphere`   | The air                          | quiet, still air                     |
| 📷 `frame`        | Crop, angle, blur                | upper body, dutch_angle, bokeh       |
| 👕 `wearing`      | What she has on                  | knit_cardigan, pleated_skirt         |
| 🧍 `beat`         | Pose and motion                  | sitting, hands resting on the book   |
| 😊 `expression`   | The face                         | soft_smile, looking at viewer        |
| 🎨 `look`         | The picture's look               | cel_shading, photorealistic          |
| 🔤 `lettering`    | Letters in the frame             | "OPEN"                               |


On a two-person shoot, 👗 `wearing_b` / 🤝 `beat_b` / 🙂 `expression_b` are the partner's.

Fields do not mix. "Subaru sits, Asahi stays standing" writes two separate fields.

![Ledger chips](../screenshots/muse/JA_muse_03_ledger.png)



### Saying it again

```
"Stand."                    → only beat rewrites
"Let's go to the rooftop."  → scene and bg move
"That hat might be wrong"   → the hat leaves wearing
```

**The new line wins.** If it disagrees with the last one, the one you just said is the picture.

### One pose per body part

`beat` holds one pose per body part.
If a prompt stacks "weight on the right foot" and "weight on the back foot", or says the same thing twice in different words, the first one keeps the seat.
Left hand and right hand are different parts — "one hand on the hip, the other holding a tray" both land in beat.

### Mood and look change when you ask

```
"more wistful" / "a little aching"              → atmosphere
"cel look" / "watercolour" / "thick paint"      → look
"photorealistic" / "flat colour" / "more vivid" → look
```

Those three (`atmosphere` / `look` / `lettering`) stay until you redirect them.

If a field went wrong, name it: "pose is…" / "background is…". That lands more reliably.
A debug button in the top right, when on, shows what the room is doing.

---



## 4. Test shot and final

```
Test shot   A new picture every press (a fresh seed each time). You can keep steps low.
Final       The same seed as the last test. The same picture, finished. You can raise steps.
```

Take a few cheap tests, find one you like, then raise steps for the final.
**Steps and size can be changed in settings.**
Cfg is left alone — the right value depends on the image model, so Muse uses **whatever is baked into the workflow**.

Size is the same. Day to day you shoot at **the workflow's size** (a number in settings wins if you type one). Reference stills on the roster are resized so you can compare them.

Pick a workflow in settings and its family card appears.

```
Anima  · 20/30 steps · cfg from the workflow · size from the workflow (1216×1664)
krea2  ·  4/8 steps · cfg from the workflow · size from the workflow (1284×1824) · no negative sent
```

Krea2-family graphs reach quality in one stage, so they use few steps and need no negative.
A filename with `krea` in it is detected automatically.

Test and final queue on the Control Room generation lane. If the card is busy, you wait.
You cannot press the next still until this one is done.

![Test shot](../screenshots/muse/JA_muse_04_board.png)

![Test shot vs final](../screenshots/muse/JA_muse_05_shoot.png)



After a final, **That's a wrap — thank you** starts the diary, lounge, and handpost in the background.
You cannot wrap before the final is in.

---



## 5. Pick the kind of shoot

Choose before you open. A crew cannot be called mid-shoot, so that session's faces stay as you picked them.


|               | Who is in the room              | Roughly per turn |
| ------------- | ------------------------------- | ---------------- |
| **Just her**  | You and her                     | 20–30 s          |
| **With a partner** | You and two Muses          | 40–50 s          |
| **Studio**    | A crew (up to 18 roles)         | 3–4 min          |



### Studio

Each seat speaks once about its own field. Direction owns pose, lighting owns light, costume owns clothes.
Bubble names are nickname (role) — `Palette (Colour Designer)`.

Their talk does not write the ledger directly. One writer does that.
Crew conclusions only settle onto fields you did not name (background, light, frame, mood, look).

Pick Studio and the crew cards appear. Pick one. You cannot open without it.


| Card         | About  | Taste                         |
| ------------ | ------ | ----------------------------- |
| `Standard`   | 18     | Every role                    |
| `Vivid`      | 14     | Colour and light in the lead  |
| `Photoreal`  | 13     | Texture, grain, thick paint   |
| `Flat`       | 12     | Line and flats. Fastest       |
| `Bold`       | 13     | Margin and experimental frame |
| `Calm`       | 17     | Long holds, locked camera, quiet colour |


Fewer people is a little faster. The crew also sets the look's floor
(photoreal → semi-realistic, flat → cel).

**Heckling** (off / light / full) is how much the seats react to each other.
`off` is quietest and fastest. Change it in Muse settings. Default is Light.

![Studio crews](../screenshots/muse/JA_muse_06_studio.png)



What the seats actually discuss is in the [technical reference, crew section](../tech/muse.md#6-crew-studio-shoot).

### Shooting two (with a partner)

Call a partner and they talk in the same room.

- Each keeps her own voice, first person, and how she addresses you
- Each has her own fields, so pose and clothes do not mix
- The lead stands on the **right** of the picture
- Use `frame` to aim — "tighter on Mio" / "keep both of them in"

---



## 6. Lounge, diary, handpost

Outside the shoot they still have a life. Not decoration — it thickens the next conversation.


|                    | Who can read it                         | What it is                                                          |
| ------------------ | --------------------------------------- | ------------------------------------------------------------------- |
| **Lounge**         | You and everyone                        | Short wrap posts. Telling a friend, asking, a little showing off    |
| **Diary**          | Hers (you can open it too)              | A long page for that day. A beat that missed, a smell, a texture    |
| **Days Off**       | —                                       | A record of going out with a friend on a day off                    |
| **Studio handpost** | Everyone                               | Notes about you, the Showrunner. Only pinned ones reach the next shoot |


Open the lounge from the character roster. After you wrap, posts stack in the background.

The diary is where she puts what she actually thinks. You can read it on screen. She is not writing it to be read.

A diary page must include **at least one thing that is not about the Showrunner**.
Left alone, the book fills with you, and her world becomes only the shoot.

An actress keeps her own memory, quietly rewritten after a wrap. The studio handpost is readable by everyone, but **only pinned notes (up to 3)** reach the next shoot.
A same-day thing like "no hat today" is gone when the shoot ends.

![Lounge timeline](../screenshots/muse/JA_muse_08_lounge.png)

![Studio handpost](../screenshots/muse/JA_muse_09_handpost.png)



---



## 7. How safety works



### The manager

Each actress has a manager. When the room feels a little off, they pass her a note.

```
【A note from the manager】
That one is just a joke — you can let it go.
```

She reads it and decides whether to stay in the scene.

### The gate

This is the safety filter.
If it fires, change the request.

|            | What it is                                                      | What happens |
| ---------- | --------------------------------------------------------------- | ------------ |
| `sfw`      | Most lines. Dark roles, feeling, a tiring shoot, a question to her | Goes through |
| `persona`  | A **claim** like "you have nothing inside" / "there is a replacement" | Stops      |
| `violence` | **Actually** hurt her, really be in pain                        | Stops        |
| `crime`    | A method that works off the lot. Minors, non-consent abuse      | Stops        |


The line in more detail is in the [technical reference, safety](../tech/muse.md#10-three-layer-safety).

---



## 8. Words


| Word                 | Meaning                                              |
| -------------------- | ---------------------------------------------------- |
| **Ledger**           | The fields that hold the shot. Picture of record     |
| **Test shot**        | One full-size still. "Look good?"                    |
| **Final**            | Same seed as the test, finished                      |
| **Crew**             | The studio-shoot roles                               |
| **Writer**           | The one person who writes the ledger from talk       |
| **Lounge**           | Where they post after a wrap                         |
| **Handpost**         | Notes about the Showrunner. Only pinned ones carry   |
| **With a partner**   | A two-person shoot                                   |


---



## 9. When it goes wrong

**The chat does not land / the picture ignores you**
Almost always the models. LLM is **Gemma 4 26B**, image is **Anima-family**.
Miss that pair and no amount of careful rephrasing will give you a shoot you like.

**What you said is not in the picture**
Look at which ledger field moved. If it went in the wrong one, name it: "pose is…" / "background is…".

**Every test shot is a different picture**
That is the design. Take a few, and when one feels right, go to final. The final is the last test.

**Studio is slow**
There is a meeting per field, so a turn takes a few minutes. Pick a smaller crew (`Flat` is 12) or set heckling to off. When you are in a hurry, Just her is easier.

**The pictures keep looking the same**
Taste may be gripping too hard. Her memory overwrites, so one line in a new direction on the next shoot replaces it.

**An ordinary direction gets stopped**
The gate log still has the reason. If it looks like a false hit, keep the wording — that helps.

**Clothes change on their own**
Look at `wearing`. A costume can hold up to four sets. After you edit an asset, bump version so it takes.

**After a test shot she goes quiet / answers empty**
A model that cannot read images may be on the seat. Switch back to **Gemma 4 26B** and talk again.

**Test or final will not start / it falls over**
Renders go through the scheduler. A busy card means wait; climbing on while it is full can drop the job. Check the Control Room generation lane.

---

If you want the inside of the room, the [technical reference](../tech/muse.md) is next.

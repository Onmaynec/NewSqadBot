"use strict";
(function () {
  const descriptions = {
    "Магазин NS Coins": "Титулы, декоративные роли, цены и наличие. Доступно только Owner.",
    "Панели ролей": "Создание, публикация и редактирование панелей самостоятельного выбора ролей.",
    "Правила ролей": "Безопасные автоматические роли после вступления и награды за уровни.",
    "Категории тикетов": "Настройка категорий поддержки и доступа кандидатов.",
    "Объявления": "Черновики, шаблоны и отложенные публикации в Discord.",
    "Мероприятия": "Игровые встречи, заявки, очереди ожидания и награды за участие.",
    "Розыгрыши": "Открытые розыгрыши, повторная проверка допуска и история победителей.",
    "Предложения": "Идеи сообщества, категории, решения и уведомления авторов.",
  };
  const $ = (tag, cls = "", txt) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (txt !== undefined && txt !== null) el.textContent = String(txt);
    return el;
  };
  function add(parent, ...nodes) {for (const node of nodes) parent.append(node);return parent;}
  function control(label, input) {
    const node = $("label", "manager-field");
    node.append($("span", "", label), input);return node;
  }
  function textInput(label, placeholder = "", max = 200) {
    const input = $("input", "portal-input");input.placeholder = placeholder;
    input.maxLength = max;return [control(label, input), input];
  }
  function textarea(label, max = 3500) {
    const input = $("textarea", "portal-textarea");input.maxLength = max;input.rows = 3;
    return [control(label, input), input];
  }
  function select(label, items) {
    const input = $("select", "portal-select");
    for (const [value, name] of items) {
      const option = $("option", "", name);option.value = value;input.append(option);
    }
    return [control(label, input), input];
  }
  function button(text, cb, type = "secondary") {
    const el = $("button", "button " + type, text);el.type = "button";
    if (cb) el.addEventListener("click", cb);return el;
  }
  function small(title, text) {
    return add($("div", "manager-empty"), $("h3", "", title), $("p", "", text));
  }
  function card(title, caption) {
    return add($("div", "manager-card"), $("h3", "", title), $("p", "portal-muted", caption));
  }
  function row(title, subtitle) {
    return add($("div", "manager-row"), add($("div", "manager-row-main"),
      $("strong", "", title), $("span", "", subtitle)));
  }
  function tag(text, color = "") {return $("span", "portal-pill " + color, text);}
  function fmtTime(seconds) {
    return seconds ? new Date(seconds * 1000).toLocaleString("ru-RU") : "—";
  }
  function isoToServerTime(input) {return input.replace("T", " ");}
  function secondsFromLocal(input) {return Math.round(new Date(input).getTime() / 1000);}
  function optionsList(type) {
    const data = window.portalData?.() || {};
    if (type === "role") return [["", "— Выберите роль —"],
      ...(data.roles || []).map(r => [r.id, r.name])];
    if (type === "channel") return [["", "— Выберите канал —"],
      ...(data.channels || []).filter(c => c.type === "text").map(c => [c.id, "#" + c.name])];
    return [];
  }
  async function api(path, options) {return window.portalAPI(path, options);}
  async function confirm(title, subtitle) {
    return new Promise(resolve => {
      const layer = $("div", "portal-modal-overlay");
      const dialog = $("div", "portal-modal");
      dialog.setAttribute("role", "alertdialog");dialog.setAttribute("aria-modal", "true");
      function done(ok) {layer.remove();resolve(ok);}
      dialog.append($("h3", "", title), $("p", "", subtitle),
        add($("div", "portal-actions"),
          button("Отмена", () => done(false)),
          button("Подтверждаю", () => done(true), "danger")));
      layer.append(dialog);document.body.append(layer);dialog.querySelector("button").focus();
    });
  }
  async function promptText(title, label, initial = "", rows = 1) {
    return new Promise(resolve => {
      const layer = $("div", "portal-modal-overlay"), dialog = $("div", "portal-modal");
      dialog.setAttribute("role", "dialog");dialog.setAttribute("aria-modal", "true");
      const input = $(rows > 1 ? "textarea" : "input",
        rows > 1 ? "portal-textarea" : "portal-input");
      input.value = initial;input.maxLength = rows > 1 ? 3500 : 400;
      if (rows > 1) input.rows = rows;
      function done(v) {layer.remove();resolve(v);}
      dialog.append($("h3", "", title),control(label,input),
        add($("div", "portal-actions"),
          button("Отмена", () => done(null)),
          button("Применить", () => done(input.value.trim()), "primary")));
      layer.append(dialog);document.body.append(layer);input.focus();
    });
  }
  function error(err) {window.portalToast(err.message || "Операция не выполнена", true);}
  function success(msg) {window.portalToast(msg || "Сохранено");}
  async function run(apiPath, body, message, needsConfirm) {
    if (needsConfirm && !await confirm(needsConfirm, "Действие изменит состояние бота или Discord.")) return false;
    try {
      const result = await api(apiPath, {method:"POST", body});
      success(result.message || message);return true;
    } catch(err){error(err);return false;}
  }
  function refresh() {void window.portalReload();}
  async function channels() {
    const result = await api("/api/portal/channels");
    return result.items.map(x => [x.id,"#"+x.name]);
  }
  async function shop() {
    const data = await api("/api/owner/shop"), wrap=$("div","portal-stack");
    const panel=card("Добавить товар","Титул для профиля или безопасная декоративная Discord-роль.");
    const form=$("form","manager-form");
    const [codeEl,code]=textInput("Уникальный код","silver_member",40);
    const [titleEl,title]=textInput("Название","Титул героя",80);
    const [descrEl,descr]=textInput("Описание","Пояснение для магазина",300);
    const [typeEl,type]=select("Тип",[["title","Титул"],["role","Discord-роль"]]);
    const [roleEl,role]=select("Декоративная роль",optionsList("role"));
    roleEl.hidden=true;
    const [priceEl,price]=textInput("Цена в NS Coins","350",7);
    price.type="number";price.min="1";price.max="1000000";
    const [stockEl,stock]=textInput("Остаток (пусто — без лимита)","",7);
    stock.type="number";stock.min="1";stock.max="1000000";
    type.addEventListener("change",()=>{roleEl.hidden=type.value!=="role";});
    form.append(codeEl,titleEl,descrEl,typeEl,roleEl,priceEl,stockEl);
    const submit=button("Создать товар",null,"primary");submit.type="submit";form.append(submit);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();submit.disabled=true;
      const ok=await run("/api/owner/shop",{code:code.value,title:title.value,
        description:descr.value,kind:type.value,role_id:type.value==="role"?role.value:null,
        price:Number(price.value),stock:stock.value?Number(stock.value):null},
        "Товар создан.");
      submit.disabled=false;if(ok)refresh();
    });
    panel.append(form);wrap.append(panel);
    const list=$("div","manager-stack");
    for(const item of data.items){
      const box=row("#"+item.id+" · "+item.title,item.code+" · "+item.price+" NS Coins");
      box.append(tag(item.kind==="role"?"Discord-роль":"Титул"),
                 tag(item.active?"Продаётся":"Скрыт",item.active?"success":"warning"),
                 button(item.active?"Снять с продажи":"Вернуть в магазин",async()=>{
                   if(await run("/api/owner/shop/"+item.id,{active:!item.active},
                     "Доступность обновлена."))refresh();
                 },item.active?"danger":"success"));
      list.append(box);
    }
    wrap.append(add(card("Каталог NS Coins",data.items.length+" товаров"),list));
    return wrap;
  }
  async function rolePanels() {
    const data=await api("/api/owner/role-panels");
    const wrap=$("div","portal-stack"),creation=card("Новая панель ролей",
      "Создайте черновик, добавьте безопасные роли и затем опубликуйте его в Discord.");
    const form=$("form","manager-form");
    const [titleL,title]=textInput("Название","Выбор ролей",100);
    const [descriptionL,description]=textarea("Описание",1000);
    description.value="Выберите роли в меню ниже.";
    const [channelL,channel]=select("Канал",optionsList("channel"));
    const [modeL,mode]=select("Режим",[["multi","Несколько ролей"],["single","Одна роль"]]);
    form.append(titleL,descriptionL,channelL,modeL);
    const submit=button("Создать черновик",null,"primary");submit.type="submit";
    form.append(submit);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();submit.disabled=true;
      const ok=await run("/api/owner/role-panels",
        {title:title.value,description:description.value,channel_id:channel.value,mode:mode.value},
        "Черновик создан.");submit.disabled=false;if(ok)refresh();
    });
    creation.append(form);wrap.append(creation);
    for(const item of data.items){
      const box=card("Панель #"+item.id+" · "+item.title,
        item.mode+" · "+(item.message_id?"Опубликована":"Черновик")
        +" · "+(item.enabled?"Активна":"Отключена"));
      const chips=$("div","manager-chips");
      for(const opt of item.options){
        const wrapper=$("div","manager-chip");
        wrapper.append(tag(opt.label+" · "+opt.category));
        wrapper.append(button("×",async()=>{
          if(await run("/api/owner/role-panels/"+item.id+"/action",
            {action:"remove",role_id:opt.role_id},"Роль удалена."))refresh();
        },"secondary"));
        chips.append(wrapper);
      }
      box.append(chips);
      if(item.enabled && item.options.length >= 2) {
        const incompatible = $("div","manager-exclusions");
        incompatible.append($("p","portal-muted","Несовместимые роли (нельзя выбрать одновременно):"));
        for(const pair of item.exclusions || []){
          const display=add($("div","manager-chip"),tag(pair.join(" / ")),
            button("×",async()=>{
              if(await run("/api/owner/role-panels/"+item.id+"/action",{
                action:"exclude",first:pair[0],second:pair[1],enabled:false
              },"Ограничение снято."))refresh();
            }));
          incompatible.append(display);
        }
        const formEx=$("form","manager-form");
        const roleOpts=item.options.map(o=>[o.role_id,o.label]);
        const [firstL,first]=select("Первая роль",roleOpts);
        const [secondL,second]=select("Вторая роль",roleOpts);
        const enable=button("Запретить сочетание",null,"secondary");enable.type="submit";
        formEx.append(firstL,secondL,enable);
        formEx.addEventListener("submit",async ev=>{
          ev.preventDefault();
          if(first.value===second.value){error(new Error("Выберите разные роли."));return;}
          if(await run("/api/owner/role-panels/"+item.id+"/action",{
            action:"exclude",first:first.value,second:second.value,enabled:true
          },"Ограничение добавлено."))refresh();
        });
        incompatible.append(formEx);box.append(incompatible);
      }
      if(item.enabled){
        const edit=$("form","manager-form");
        const [roleL,role]=select("Роль для добавления",optionsList("role"));
        const [labelL,label]=textInput("Подпись","Игры",80);
        const [categoryL,category]=select("Категория",[
          ["games","Игры"],["notifications","Уведомления"],
          ["interests","Интересы"],["decorative","Декоративные"]]);
        edit.append(roleL,labelL,categoryL);
        const plus=button("Добавить роль",null,"primary");plus.type="submit";edit.append(plus);
        edit.addEventListener("submit",async ev=>{
          ev.preventDefault();plus.disabled=true;
          const ok=await run("/api/owner/role-panels/"+item.id+"/action",{
            action:"add",role_id:role.value,label:label.value,category:category.value
          },"Роль добавлена.");plus.disabled=false;if(ok)refresh();
        });
        box.append(edit);
        box.append(add($("div","portal-actions"),
          button(item.message_id?"Обновить в Discord":"Опубликовать",async()=>{
            if(await run("/api/owner/role-panels/"+item.id+"/action",
              {action:"publish"},"Панель опубликована.","Опубликовать панель?"))refresh();
          },"success"),
          button("Отключить",async()=>{
            if(await run("/api/owner/role-panels/"+item.id+"/action",
              {action:"disable"},"Панель отключена.","Отключить панель?"))refresh();
          },"danger")));
      }
      wrap.append(box);
    }
    return wrap;
  }
  async function roleRules() {
    const data=await api("/api/owner/role-rules");
    const wrap=$("div","portal-stack");
    const box=card("Автоматическая выдача после одобрения",
      "Только безопасные роли ниже роли бота. Изменения применяются к будущим одобрениям.");
    const form=$("form","manager-form");
    const [roleL,role]=select("Выберите роль",optionsList("role"));
    const addRole=button("Добавить автоматическую роль",null,"primary");
    addRole.type="submit";form.append(roleL,addRole);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();
      if(await run("/api/owner/role-rules",{category:"autorole",role_id:role.value,enabled:true},
        "Роль добавлена."))refresh();
    });
    box.append(form);
    for(const id of data.autoroles){
      box.append(add($("div","manager-row"),
        $("span","",optionsList("role").find(opt=>opt[0]===id)?.[1]||id),
        button("Убрать",async()=>{
          if(await run("/api/owner/role-rules",
            {category:"autorole",role_id:id,enabled:false},"Роль убрана."))refresh();
        },"danger")));
    }
    wrap.append(box);
    const rewards=card("Награды за уровни","Автоматическая выдача по достижении выбранного уровня.");
    const form2=$("form","manager-form");
    const [lvL,level]=textInput("Уровень","10",5);level.type="number";level.min="1";level.max="10000";
    const [role2L,role2]=select("Наградная роль",optionsList("role"));
    const addReward=button("Добавить награду",null,"primary");addReward.type="submit";
    form2.append(lvL,role2L,addReward);
    form2.addEventListener("submit",async ev=>{
      ev.preventDefault();
      if(await run("/api/owner/role-rules",
        {category:"level",level:Number(level.value),role_id:role2.value,enabled:true},
        "Награда добавлена."))refresh();
    });
    rewards.append(form2);
    for(const reward of data.level_rewards){
      rewards.append(add($("div","manager-row"),
        $("span","","Уровень "+reward.level+" → "+(
          optionsList("role").find(opt=>opt[0]===reward.role_id)?.[1]||reward.role_id)),
        button("Убрать",async()=>{
          if(await run("/api/owner/role-rules",{
            category:"level",level:reward.level,role_id:reward.role_id,enabled:false
          },"Награда удалена."))refresh();
        },"danger")));
    }
    wrap.append(rewards);
    return wrap;
  }
  async function ticketCategories() {
    const data=await api("/api/owner/ticket-categories");
    const wrap=$("div","portal-stack"),box=card("Новая категория",
      "Название, описание и роль сотрудников, которые будут отвечать за обращения.");
    const form=$("form","manager-form");
    const [keyL,key]=textInput("Ключ на латинице","technical-help",40);
    const [titleL,title]=textInput("Название","Техническая помощь",90);
    const [descL,desc]=textInput("Описание","Поддержка сайта",300);
    const [roleL,role]=select("Роль поддержки",optionsList("role"));
    const flag=add($("label","manager-check"),$("input"),$("span","","Разрешить кандидатам"));
    const check=flag.querySelector("input");check.type="checkbox";
    const submit=button("Создать категорию",null,"primary");submit.type="submit";
    form.append(keyL,titleL,descL,roleL,flag,submit);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();submit.disabled=true;
      const ok=await run("/api/owner/ticket-categories",{
        key:key.value,title:title.value,description:desc.value,
        support_role_id:role.value||null,candidate_allowed:check.checked},
        "Категория добавлена.");submit.disabled=false;if(ok)refresh();
    });
    box.append(form);wrap.append(box);
    for(const item of data.items){
      const line=card("#"+item.id+" · "+item.title,item.key+" · "+item.description);
      line.append(add($("div","portal-actions"),
        tag(item.enabled?"Активна":"Отключена",item.enabled?"success":"warning"),
        tag(item.candidate_allowed?"Доступна кандидатам":"Только участникам"),
        button(item.enabled?"Отключить":"Включить",async()=>{
          if(await run("/api/owner/ticket-categories/"+item.id,{
            enabled:!item.enabled,candidate_allowed:item.candidate_allowed},"Категория обновлена."))refresh();
        }),
        button(item.candidate_allowed?"Запретить кандидатам":"Разрешить кандидатам",async()=>{
          if(await run("/api/owner/ticket-categories/"+item.id,{
            enabled:item.enabled,candidate_allowed:!item.candidate_allowed},"Доступ обновлён."))refresh();
        })));
      wrap.append(line);
    }
    return wrap;
  }
  async function announcements() {
    const data=await api("/api/owner/announcements");
    const wrap=$("div","portal-stack"),box=card("Создать черновик",
      "Сначала создайте объявление, затем отправьте или запланируйте его.");
    const form=$("form","manager-form");
    const [titleL,title]=textInput("Заголовок","Новости NewSqad",120);
    const [bodyL,body]=textarea("Текст",3500);
    const [channelL,channel]=select("Канал публикации",optionsList("channel"));
    const [kindL,kind]=select("Формат",[["embed","Embed"],["text","Обычный текст"]]);
    const [colorL,color]=select("Цвет Embed",[
      ["4884215","Синий"],["0","Чёрный"],["65280","Зелёный"],["16711680","Красный"],
      ["16777215","Белый"]]);
    form.append(titleL,bodyL,channelL,kindL,colorL);
    const submit=button("Создать черновик",null,"primary");submit.type="submit";form.append(submit);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();submit.disabled=true;
      const ok=await run("/api/owner/announcements",{
        title:title.value,body:body.value,channel_id:channel.value,
        kind:kind.value,color:Number(color.value)},"Черновик добавлен.");
      submit.disabled=false;if(ok)refresh();
    });
    box.append(form);wrap.append(box);
    for(const item of data.items){
      const line=card("#"+item.id+" · "+item.title,
        item.status+" · "+(item.due_at?fmtTime(item.due_at):"Без даты"));
      const preview=$("div","manager-preview");
      preview.textContent=item.body;preview.style.borderLeftColor="#"+item.color.toString(16).padStart(6,"0");
      line.append(preview);
      const actions=$("div","portal-actions");
      if(["draft","failed"].includes(item.status)){
        actions.append(button("Редактировать",async()=>{
          const title=await promptText("Заголовок", "Заголовок",item.title);
          if(title===null)return;
          const text=await promptText("Текст объявления","Текст",item.body,5);
          if(text===null)return;
          if(await run("/api/owner/announcements/"+item.id+"/action",
            {action:"edit",title,body:text},"Черновик обновлён."))refresh();
        }));
      }
      if(item.status==="draft"){
        actions.append(button("Отправить",async()=>{
          if(await run("/api/owner/announcements/"+item.id+"/action",
            {action:"send",confirm:"SEND"},"Отправлено.","Отправить объявление сейчас?"))refresh();
        },"success"));
        actions.append(button("Запланировать",async()=>{
          const time=await promptText("Отложенная публикация","Дата и время: ГГГГ-ММ-ДД ЧЧ:ММ");
          if(!time)return;
          const epoch=secondsFromLocal(time.replace(" ","T"));
          if(!Number.isFinite(epoch)){error(new Error("Неверная дата."));return;}
          if(await run("/api/owner/announcements/"+item.id+"/action",
            {action:"schedule",due_at:epoch},"Дата публикации установлена."))refresh();
        }));
      }
      actions.append(button("Сохранить шаблон",async()=>{
        const slug=await promptText("Сохранение шаблона","Ключ: a-z, цифры, - или _");
        if(slug && await run("/api/owner/announcements/"+item.id+"/action",
          {action:"template",slug},"Шаблон сохранён."))refresh();
      }));
      actions.append(button("Дублировать",async()=>{
        if(await run("/api/owner/announcements/"+item.id+"/action",
          {action:"duplicate"},"Черновик скопирован."))refresh();
      }));
      if(["draft","scheduled","failed"].includes(item.status)){
        actions.append(button("Отменить",async()=>{
          if(await run("/api/owner/announcements/"+item.id+"/action",
            {action:"cancel"},"Публикация отменена.","Отменить публикацию?"))refresh();
        },"danger"));
      }
      line.append(actions);wrap.append(line);
    }
    return wrap;
  }
  async function eventsPage() {
    const [data,catalog]=await Promise.all([api("/api/portal/events"),channels()]);
    const wrap=$("div","portal-stack"),box=card("Создать мероприятие",
      "Время начала указывайте по часовой зоне бота Europe/Tallinn.");
    const form=$("form","manager-form");
    const [titleL,title]=textInput("Название","Командный турнир",100);
    const [descL,desc]=textarea("Описание",800);
    const [channelL,channel]=select("Канал",catalog);
    const [startsL,starts]=textInput("Дата и время (ГГГГ-ММ-ДД ЧЧ:ММ)","2026-12-01 19:00",25);
    const [durationL,duration]=textInput("Длительность, минут","120",5);
    duration.type="number";duration.value="120";
    const [capacityL,capacity]=textInput("Мест (0 — без лимита)","16",5);capacity.type="number";capacity.value="0";
    const [xpL,xp]=textInput("Награда XP","0",5);xp.type="number";xp.value="0";
    const [coinsL,coins]=textInput("Награда NS Coins","0",5);coins.type="number";coins.value="0";
    form.append(titleL,descL,channelL,startsL,durationL,capacityL,xpL,coinsL);
    const submit=button("Создать мероприятие",null,"primary");submit.type="submit";form.append(submit);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();submit.disabled=true;
      const ok=await run("/api/portal/events",{
        title:title.value,description:desc.value,channel_id:channel.value,starts:isoToServerTime(starts.value),
        duration:Number(duration.value),capacity:Number(capacity.value),
        reward_xp:Number(xp.value),reward_coins:Number(coins.value),waitlist:true},
        "Мероприятие создано.");submit.disabled=false;if(ok)refresh();
    });
    box.append(form);wrap.append(box);
    for(const item of data.items){
      const line=card("#"+item.id+" · "+item.title,
        "Начало: "+fmtTime(item.starts_at)+" · "+item.status+" · Участники: "+item.confirmed);
      const actions=$("div","portal-actions");
      actions.append(button("Участники",async()=>{
        try{
          const result=await api("/api/portal/events/"+item.id);
          const dialog=card("Участники #"+item.id,"Подтверждение присутствия с выдачей наград.");
          for(const member of result.members){
            const rr=row(member.id,member.status+(member.attended?" · Подтверждён":""));
            if(!member.attended && member.status==="registered"){
              rr.append(button("Подтвердить участие",async()=>{
                if(await run("/api/portal/events/"+item.id+"/action",
                  {action:"attendance",user_id:member.id},"Участие подтверждено.","Начислить награды?"))refresh();
              },"success"));
            }
            dialog.append(rr);
          }
          const overlay=$("div","portal-modal-overlay");const modal=$("div","portal-modal manager-wide");
          modal.append(dialog,button("Закрыть",()=>overlay.remove()));
          overlay.append(modal);document.body.append(overlay);
        }catch(err){error(err);}
      }));
      if(["scheduled","live"].includes(item.status)){
        actions.append(button("Отменить",async()=>{
          const reason=await promptText("Причина отмены","Причина");
          if(reason && await run("/api/portal/events/"+item.id+"/action",{
            action:"cancel",reason,confirm:"CANCEL"},"Мероприятие отменено.","Точно отменить мероприятие?"))refresh();
        },"danger"));
      }
      line.append(actions);wrap.append(line);
    }
    return wrap;
  }
  async function giveaways() {
    const [data,catalog]=await Promise.all([api("/api/portal/giveaways"),channels()]);
    const wrap=$("div","portal-stack"),box=card("Открыть розыгрыш","Укажите приз, правила и дату завершения.");
    const form=$("form","manager-form");
    const [prizeL,prize]=textInput("Приз","VIP-титул",200);
    const [rulesL,rules]=textarea("Условия",500);
    const [channelL,channel]=select("Канал",catalog);
    const [endsL,ends]=textInput("Завершение (ГГГГ-ММ-ДД ЧЧ:ММ)","2026-12-01 19:00",25);
    const [countL,count]=textInput("Победителей","1",4);count.type="number";count.value="1";
    form.append(prizeL,rulesL,channelL,endsL,countL);
    const submit=button("Создать розыгрыш",null,"primary");submit.type="submit";form.append(submit);
    form.addEventListener("submit",async ev=>{
      ev.preventDefault();submit.disabled=true;
      const ok=await run("/api/portal/giveaways",{prize:prize.value,conditions:rules.value,
        channel_id:channel.value,ends:isoToServerTime(ends.value),
        winners:Number(count.value),min_level:0},"Розыгрыш создан.");
      submit.disabled=false;if(ok)refresh();
    });
    box.append(form);wrap.append(box);
    for(const item of data.items){
      const line=card("#"+item.id+" · "+item.prize,
        item.status+" · Завершение "+fmtTime(item.ends_at)+" · Участников: "+item.entries);
      line.append($("p","portal-muted","Победители: "+(item.winners.length?item.winners.join(", "):"Не выбраны")));
      const actions=$("div","portal-actions");
      if(item.status==="open"){
        actions.append(button("Выбрать победителей",async()=>{
          if(await run("/api/portal/giveaways/"+item.id+"/action",{
            action:"draw",confirm:"DRAW"},"Итоги сохранены.","Завершить розыгрыш и выбрать победителей?"))refresh();
        },"danger"));
      }
      if(item.status==="ended"){
        actions.append(button("Повторный выбор",async()=>{
          const reason=await promptText("Причина нового выбора","Обязательная причина");
          if(reason && await run("/api/portal/giveaways/"+item.id+"/action",{
            action:"reroll",reason,confirm:"DRAW"},"Повторный выбор сохранён.","Переизбрать победителей?"))refresh();
        },"danger"));
      }
      line.append(actions);wrap.append(line);
    }
    return wrap;
  }
  async function suggestions() {
    const wrap=$("div","portal-stack"),toolbar=$("div","portal-toolbar");
    const [statusL,status]=select("Статус",[["all","Все"],["new","Новые"],["under_review","На рассмотрении"],
      ["accepted","Принятые"],["rejected","Отклонённые"],["implemented","Реализованные"]]);
    const [categoryL,category]=select("Категория",[["all","Все"],["general","Общее"],["games","Игры"],
      ["events","Мероприятия"],["server","Сервер"],["other","Другое"]]);
    toolbar.append(statusL,categoryL);wrap.append(toolbar);
    const results=$("div","portal-stack");
    async function refreshResults(){
      try{
        const query=new URLSearchParams({status:status.value,category:category.value});
        const data=await api("/api/portal/suggestions?"+query);
        results.replaceChildren();
        if(!data.items.length)results.append(small("Нет предложений","Измени фильтры или проверь настройки."));
        for(const item of data.items){
          const line=card("#"+item.id+" · "+item.topic,
            "Автор: "+item.author_id+" · "+item.category+" · "+item.status);
          line.append($("p","manager-preview",item.description));
          line.append($("div","portal-muted","Голоса: за "+item.votes_for+" / против "+item.votes_against));
          const [flag,flagValue]=select("Новое решение",[
            ["under_review","На рассмотрении"],["accepted","Принять"],
            ["rejected","Отклонить"],["implemented","Реализовано"]]);
          const actions=add($("div","portal-actions"),flag,
            button("Сохранить решение",async()=>{
              const reason=await promptText("Пояснение администрации","Причина изменения статуса");
              if(reason && await run("/api/portal/suggestions/"+item.id+"/action",
                {status:flagValue.value,reason},"Автор будет уведомлён."))await refreshResults();
            },"primary"));
          line.append(actions);results.append(line);
        }
      }catch(err){results.replaceChildren(small("Ошибка",err.message));}
    }
    status.addEventListener("change",()=>{void refreshResults();});
    category.addEventListener("change",()=>{void refreshResults();});
    await refreshResults();wrap.append(results);return wrap;
  }
  const loaders={
    "Магазин NS Coins":shop,"Панели ролей":rolePanels,"Правила ролей":roleRules,
    "Категории тикетов":ticketCategories,
    "Объявления":announcements,"Мероприятия":eventsPage,"Розыгрыши":giveaways,
    "Предложения":suggestions,
  };
  window.Management={loaders,descriptions};
})();

'use strict';
function translateGuide(language){I18n.set(language||'en');document.querySelector('[data-flow=router]').setAttribute('aria-label',I18n.flow(['Modem','Mac','Router','Client']));document.querySelector('[data-flow=hotspot]').setAttribute('aria-label',I18n.flow(['Modem','Mac','Client'])+' · '+I18n.t('Wi-Fi hotspot'));}
translateGuide(I18n.get());
fetch('/api/session',{credentials:'same-origin',cache:'no-store'}).then(function(r){if(!r.ok)throw new Error();return r.json();}).then(function(s){translateGuide(s.language);}).catch(function(){translateGuide(I18n.get());});

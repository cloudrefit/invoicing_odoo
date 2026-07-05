/** @odoo-module **/

import { browser } from "@web/core/browser/browser";

let saveTimeout = null;

browser.addEventListener("focusout", (ev) => {
    if (!document.querySelector('.o_settings_container')) return;
    
    // Check if the target is an input, select, or checkbox related to cloudrefit
    if (ev.target && ev.target.matches('input, select, textarea') && 
       (ev.target.id && ev.target.id.includes('cloudrefit_') || 
        ev.target.name && ev.target.name.includes('cloudrefit_'))) {
        
        // Clear any existing timeout to debounce
        if (saveTimeout) clearTimeout(saveTimeout);
        
        saveTimeout = setTimeout(() => {
            // Find the save button (works for Odoo 16/17/18)
            const saveBtns = Array.from(document.querySelectorAll('button')).filter(b => 
                b.classList.contains('o_form_button_save') || 
                (b.textContent && b.textContent.trim() === 'Save')
            );
            
            for (const btn of saveBtns) {
                if (!btn.disabled && btn.offsetParent !== null) { // if visible and enabled
                    btn.click();
                    break;
                }
            }
        }, 800); // 800ms debounce
    }
});

// Also handle checkbox changes directly, as they might not emit focusout reliably
browser.addEventListener("change", (ev) => {
    if (!document.querySelector('.o_settings_container')) return;
    
    if (ev.target && ev.target.type === 'checkbox' && 
       (ev.target.id && ev.target.id.includes('cloudrefit_') || 
        ev.target.name && ev.target.name.includes('cloudrefit_'))) {
        
        if (saveTimeout) clearTimeout(saveTimeout);
        
        saveTimeout = setTimeout(() => {
            const saveBtns = Array.from(document.querySelectorAll('button')).filter(b => 
                b.classList.contains('o_form_button_save') || 
                (b.textContent && b.textContent.trim() === 'Save')
            );
            
            for (const btn of saveBtns) {
                if (!btn.disabled && btn.offsetParent !== null) {
                    btn.click();
                    break;
                }
            }
        }, 800);
    }
});

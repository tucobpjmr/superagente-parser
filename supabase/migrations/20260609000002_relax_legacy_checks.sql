-- Fase 0 — Rimozione CHECK legacy troppo rigidi su documenti.
-- categoria: il contratto API del parser la definisce "categoria libera";
--   il CHECK la limitava a 5 valori fissi.
-- tipo_file: limitato a pdf/docx/txt, ma il parser supporta 16 estensioni
--   (xlsx, pptx, html, immagini OCR, ...). La validazione del formato
--   avviene nel parser (EXT_TO_MIME), non nel database.

alter table documenti drop constraint if exists documenti_categoria_check;
alter table documenti drop constraint if exists documenti_tipo_file_check;
